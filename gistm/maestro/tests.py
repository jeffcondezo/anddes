from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from maestro.activity import eventos_monitoreo_empresa
from maestro.avisos import avisar_nueva_carga, contar_avisos_no_leidos
from maestro.indicadores import (
    avance_por_principios,
    chart_documentos_estados,
    chart_temas,
    curva_avance,
    documentos_indicadores,
    documentos_prioritarios,
    filtrar_documentos,
    tablero_estandar,
    tablero_progreso,
)
from maestro.models import (
    Actividad,
    Acuerdo,
    AcuerdoNotificacionEnvio,
    AlcanceActividad,
    Area,
    AvisoConsultor,
    Criterio,
    DocumentoCarga,
    Empresa,
    EmpresaDocumento,
    EmpresaDocumentoCriterio,
    EmpresaDocumentoPregunta,
    EmpresaNotificacionRegla,
    EstadoAcuerdo,
    EstadoActividad,
    EstadoRevisionCarga,
    EstadoRevisionCriterio,
    EstadoRevisionPregunta,
    DocumentoMensaje,
    OrigenEmpresaDocumento,
    TipoDocumentoMensaje,
    PerfilUsuario,
    Pregunta,
    Principio,
    Requisito,
    Revision,
    Tema,
    TipoActividad,
    TipoAvisoConsultor,
    TipoNotificacionAcuerdo,
    TIPOS_CLIENTE,
    TipoUsuario,
    sincronizar_estado_criterio_desde_preguntas,
)
from maestro.permissions import empresas_visibles, puede_ver_empresa
from maestro.progress import (
    calcular_progreso_revision,
    revision_activa_empresa,
    revisiones_activas_empresa,
)

User = get_user_model()


def _user(email: str, **kwargs) -> User:
    return User.objects.create_user(
        username=email,
        email=email,
        password="test-pass-123",
        **kwargs,
    )


def _perfil(user, tipo: str, *, empresa=None, empresas=None, revisiones=None) -> PerfilUsuario:
    perfil = PerfilUsuario.objects.create(
        user=user,
        tipo=tipo,
        activo=True,
        empresa=empresa if tipo in TIPOS_CLIENTE else None,
    )
    if tipo == TipoUsuario.GESTOR and empresas:
        perfil.empresas.set(empresas)
    if tipo in TIPOS_CLIENTE and revisiones is not None:
        perfil.revisiones.set(revisiones)
    return perfil


class FixturesMixin:
    def setUp(self):
        super().setUp()
        self.empresa = Empresa.objects.create(nombre="Minera Demo", ruc="20111111111")
        self.otra_empresa = Empresa.objects.create(nombre="Otra Minera", ruc="20222222222")
        self.area = Area.objects.create(empresa=self.empresa, nombre="Operaciones")
        self.revision = Revision.objects.create(
            empresa=self.empresa, anio=2026, nombre="R2026", activo=True
        )
        # Snapshot se crea tras el criterio (abajo), vía asegurar en tests que lo necesiten.

        self.tema = Tema.objects.create(codigo="T1", nombre="Tema 1")
        self.principio = Principio.objects.create(
            codigo="P1", descripcion="Principio 1", tema=self.tema
        )
        self.requisito = Requisito.objects.create(
            codigo="R1", descripcion="Requisito 1", principio=self.principio
        )
        self.criterio = Criterio.objects.create(
            codigo="C1",
            descripcion="Criterio 1",
            requisito=self.requisito,
        )
        self.pregunta = Pregunta.objects.create(
            codigo="Q1",
            texto="¿Se evidencia el criterio 1?",
            criterio=self.criterio,
            orden=1,
            activo=True,
        )
        self.doc = EmpresaDocumento.objects.create(
            revision=self.revision,
            codigo="DOC-01",
            nombre="Plan de cierre",
            tipo_documento="PDF",
            origen=OrigenEmpresaDocumento.CATALOGO,
            activo=True,
        )
        self.vinculo = EmpresaDocumentoCriterio.objects.create(
            revision=self.revision,
            empresa_documento=self.doc,
            criterio=self.criterio,
            area=self.area,
        )
        self.respuesta = EmpresaDocumentoPregunta.objects.get(
            vinculo_criterio=self.vinculo, pregunta=self.pregunta
        )
        from maestro.models import crear_snapshot_catalogo_revision

        crear_snapshot_catalogo_revision(self.revision)

        self.admin = _user("admin@test.local", first_name="Ana", last_name="Admin")
        _perfil(self.admin, TipoUsuario.ADMINISTRADOR)

        self.gestor = _user("gestor@test.local", first_name="Gina", last_name="Gestor")
        _perfil(self.gestor, TipoUsuario.GESTOR, empresas=[self.empresa])

        self.cliente = _user("cliente@test.local", first_name="Carlos", last_name="Cliente")
        self.perfil_cliente = _perfil(
            self.cliente,
            TipoUsuario.CLIENTE_CARGA,
            empresa=self.empresa,
            revisiones=[self.revision],
        )

    def _aprobar_criterio_via_preguntas(self, vinculo=None):
        vinculo = vinculo or self.vinculo
        for resp in EmpresaDocumentoPregunta.objects.filter(vinculo_criterio=vinculo):
            resp.estado_revision = EstadoRevisionPregunta.CUMPLE
            resp.observacion = ""
            resp.save(
                update_fields=["estado_revision", "observacion", "actualizado_en"]
            )
        sincronizar_estado_criterio_desde_preguntas(vinculo)
        vinculo.refresh_from_db()
        return vinculo


@override_settings(
    AZURE_AD_REQUIRE_LOGIN=False,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
class ProgressTests(FixturesMixin, TestCase):
    def test_revision_activa(self):
        self.assertEqual(revision_activa_empresa(self.empresa.pk), self.revision)

    def test_snapshot_catalogo_no_cambia_con_edicion_posterior(self):
        from maestro.models import RevisionCatalogoCriterio
        from maestro.progress import calcular_progreso_revision

        snap = RevisionCatalogoCriterio.objects.get(
            revision=self.revision, criterio=self.criterio
        )
        self.assertEqual(snap.codigo, "C1")
        self.assertIn("Criterio 1", snap.descripcion)

        # Edición del catálogo vivo no debe alterar el snapshot ni el árbol histórico.
        self.criterio.codigo = "C1-FUSIONADO"
        self.criterio.descripcion = "Texto nuevo que no debe verse en la revisión"
        self.criterio.save()

        snap.refresh_from_db()
        self.assertEqual(snap.codigo, "C1")
        self.assertIn("Criterio 1", snap.descripcion)

        progreso = calcular_progreso_revision(self.revision)
        codigos = [c.codigo for c in progreso.criterios_huerfanos] + [
            n.codigo
            for t in progreso.temas
            for p in t.children
            for r in p.children
            for n in r.children
        ]
        self.assertIn("C1", codigos)
        self.assertNotIn("C1-FUSIONADO", codigos)

    def test_varias_revisiones_activas_en_paralelo(self):
        otra = Revision.objects.create(
            empresa=self.empresa,
            anio=self.revision.anio,
            nombre="Paralela",
            activo=True,
        )
        activas = list(revisiones_activas_empresa(self.empresa.pk))
        self.assertEqual(len(activas), 2)
        self.assertIn(self.revision, activas)
        self.assertIn(otra, activas)
        inactiva = Revision.objects.create(
            empresa=self.empresa,
            anio=self.revision.anio,
            nombre="Inactiva",
            activo=False,
        )
        activas = list(revisiones_activas_empresa(self.empresa.pk))
        self.assertEqual(len(activas), 2)
        self.assertNotIn(inactiva, activas)

    def test_carga_pendiente_no_cuenta_en_avance(self):
        DocumentoCarga.objects.create(
            empresa_documento=self.doc,
            archivo=SimpleUploadedFile("ev.pdf", b"%PDF-1.4 demo"),
            subido_por=self.cliente,
            activo=True,
            estado_revision=EstadoRevisionCarga.PENDIENTE,
        )
        progreso = calcular_progreso_revision(self.revision)
        self.assertEqual(progreso.docs_total, 1)
        self.assertEqual(progreso.docs_cargados, 0)
        self.assertEqual(progreso.docs_por_revisar, 1)

    def test_carga_aprobada_cuenta_en_avance(self):
        DocumentoCarga.objects.create(
            empresa_documento=self.doc,
            archivo=SimpleUploadedFile("ev.pdf", b"%PDF-1.4 demo"),
            subido_por=self.cliente,
            activo=True,
            estado_revision=EstadoRevisionCarga.APROBADO,
            revisado_por=self.gestor,
            revisado_en=timezone.now(),
        )
        self._aprobar_criterio_via_preguntas()
        progreso = calcular_progreso_revision(self.revision)
        self.assertEqual(progreso.docs_cargados, 1)
        self.assertEqual(progreso.docs_pct, 100.0)
        self.assertEqual(progreso.criterios_completos, 1)

    def test_criterio_sin_preguntas_no_completo(self):
        EmpresaDocumentoPregunta.objects.filter(vinculo_criterio=self.vinculo).delete()
        Pregunta.objects.filter(criterio=self.criterio).delete()
        self.vinculo.estado_revision = EstadoRevisionCriterio.APROBADO
        self.vinculo.save(
            update_fields=["estado_revision", "actualizado_en"], skip_seed=True
        )
        progreso = calcular_progreso_revision(self.revision)
        self.assertEqual(progreso.criterios_completos, 0)

    def test_preguntas_parciales_no_aprueban_criterio(self):
        q2 = Pregunta.objects.create(
            codigo="Q2",
            texto="Segunda pregunta",
            criterio=self.criterio,
            orden=2,
        )
        # Re-sembrar
        self.vinculo.save()
        r1 = EmpresaDocumentoPregunta.objects.get(
            vinculo_criterio=self.vinculo, pregunta=self.pregunta
        )
        r2 = EmpresaDocumentoPregunta.objects.get(
            vinculo_criterio=self.vinculo, pregunta=q2
        )
        r1.estado_revision = EstadoRevisionPregunta.CUMPLE
        r1.save(update_fields=["estado_revision", "actualizado_en"])
        sincronizar_estado_criterio_desde_preguntas(self.vinculo)
        self.vinculo.refresh_from_db()
        self.assertNotEqual(self.vinculo.estado_revision, EstadoRevisionCriterio.APROBADO)
        progreso = calcular_progreso_revision(self.revision)
        self.assertEqual(progreso.criterios_completos, 0)
        # Avance parcial: 1 de 2 preguntas ya suma al % global de criterios.
        self.assertEqual(progreso.criterios_pct, 50.0)

        r2.estado_revision = EstadoRevisionPregunta.CUMPLE
        r2.save(update_fields=["estado_revision", "actualizado_en"])
        sincronizar_estado_criterio_desde_preguntas(self.vinculo)
        self.vinculo.refresh_from_db()
        self.assertEqual(self.vinculo.estado_revision, EstadoRevisionCriterio.APROBADO)
        progreso = calcular_progreso_revision(self.revision)
        self.assertEqual(progreso.criterios_completos, 1)
        self.assertEqual(progreso.criterios_pct, 100.0)

    def test_aprobacion_parcial_suma_criterio_no_documento(self):
        from maestro.models import crear_snapshot_catalogo_revision

        c2 = Criterio.objects.create(
            codigo="C2",
            descripcion="Criterio 2",
            requisito=self.requisito,
        )
        Pregunta.objects.create(
            codigo="Q-C2", texto="Pregunta C2", criterio=c2, orden=1
        )
        # Simula que ambos criterios existían al abrir la revisión.
        crear_snapshot_catalogo_revision(self.revision, force=True)
        EmpresaDocumentoCriterio.objects.create(
            revision=self.revision,
            empresa_documento=self.doc,
            criterio=c2,
            area=self.area,
        )
        DocumentoCarga.objects.create(
            empresa_documento=self.doc,
            archivo=SimpleUploadedFile("ev.pdf", b"%PDF-1.4 demo"),
            subido_por=self.cliente,
            activo=True,
            estado_revision=EstadoRevisionCarga.PENDIENTE,
        )
        self._aprobar_criterio_via_preguntas()
        from maestro.models import sincronizar_estado_carga_documento

        carga = sincronizar_estado_carga_documento(self.doc)
        self.assertEqual(carga.estado_revision, EstadoRevisionCarga.PARCIAL)

        progreso = calcular_progreso_revision(self.revision)
        self.assertEqual(progreso.criterios_completos, 1)
        self.assertEqual(progreso.criterios_total, 2)
        self.assertEqual(progreso.docs_cargados, 0)
        self.assertEqual(progreso.docs_por_revisar, 1)


@override_settings(
    AZURE_AD_REQUIRE_LOGIN=False,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
class AvisosTests(FixturesMixin, TestCase):
    def test_avisar_nueva_carga_crea_avisos_para_consultores(self):
        carga = DocumentoCarga.objects.create(
            empresa_documento=self.doc,
            archivo=SimpleUploadedFile("ev.pdf", b"%PDF-1.4 demo"),
            subido_por=self.cliente,
            activo=True,
            estado_revision=EstadoRevisionCarga.PENDIENTE,
        )
        creados = avisar_nueva_carga(carga=carga, actor=self.cliente)
        self.assertGreaterEqual(len(creados), 2)
        destinatarios = {a.destinatario_id for a in creados}
        self.assertIn(self.admin.pk, destinatarios)
        self.assertIn(self.gestor.pk, destinatarios)
        self.assertNotIn(self.cliente.pk, destinatarios)
        self.assertEqual(contar_avisos_no_leidos(self.gestor), 1)
        aviso = AvisoConsultor.objects.get(destinatario=self.gestor)
        self.assertEqual(aviso.tipo, TipoAvisoConsultor.CARGA)
        self.assertFalse(aviso.leido)


@override_settings(
    AZURE_AD_REQUIRE_LOGIN=False,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
class MonitoreoTests(FixturesMixin, TestCase):
    def test_incluye_aprobacion_y_rechazo_con_actor(self):
        aprobada = DocumentoCarga.objects.create(
            empresa_documento=self.doc,
            archivo=SimpleUploadedFile("ok.pdf", b"%PDF-1.4 ok"),
            subido_por=self.cliente,
            activo=False,
            estado_revision=EstadoRevisionCarga.APROBADO,
            revisado_por=self.gestor,
            revisado_en=timezone.now() - timedelta(hours=1),
        )
        DocumentoCarga.objects.create(
            empresa_documento=self.doc,
            archivo=SimpleUploadedFile("bad.pdf", b"%PDF-1.4 bad"),
            subido_por=self.cliente,
            activo=True,
            estado_revision=EstadoRevisionCarga.RECHAZADO,
            motivo_rechazo="Falta firma del responsable.",
            revisado_por=self.admin,
            revisado_en=timezone.now(),
        )
        eventos = eventos_monitoreo_empresa(self.empresa.pk, limite=50)
        tipos = {e.tipo for e in eventos}
        self.assertIn("carga", tipos)
        self.assertIn("aprobacion", tipos)
        self.assertIn("rechazo", tipos)
        aprob = next(e for e in eventos if e.tipo == "aprobacion")
        rech = next(e for e in eventos if e.tipo == "rechazo")
        self.assertIn("Gina", aprob.actor)
        self.assertIn("Ana", rech.actor)
        self.assertIn("Falta firma", rech.detalle)
        self.assertEqual(aprobada.estado_revision, EstadoRevisionCarga.APROBADO)


@override_settings(
    AZURE_AD_REQUIRE_LOGIN=False,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
class PermissionsTests(FixturesMixin, TestCase):
    def test_gestor_solo_ve_empresas_asignadas(self):
        visibles = list(empresas_visibles(self.gestor))
        self.assertEqual(visibles, [self.empresa])
        self.assertTrue(puede_ver_empresa(self.gestor, self.empresa))
        self.assertFalse(puede_ver_empresa(self.gestor, self.otra_empresa))

    def test_admin_ve_todas(self):
        ids = set(empresas_visibles(self.admin).values_list("pk", flat=True))
        self.assertIn(self.empresa.pk, ids)
        self.assertIn(self.otra_empresa.pk, ids)


@override_settings(
    AZURE_AD_REQUIRE_LOGIN=False,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
class AcuerdoActividadTests(FixturesMixin, TestCase):
    def test_crear_actividad_y_acuerdo(self):
        from .models import Responsable

        responsable = Responsable.objects.create(
            area=self.area,
            nombre="Ana Pérez",
            dni="12345678",
            telefono="999888777",
            correo="ana@example.com",
        )
        actividad = Actividad.objects.create(
            titulo="Reunión semanal",
            descripcion="Seguimiento de evidencias",
            tipo=TipoActividad.GENERAL,
            alcance=AlcanceActividad.CON_CLIENTE,
            empresa=self.empresa,
            responsable=responsable,
            area=self.area,
            inicio=timezone.now() + timedelta(days=1),
            estado=EstadoActividad.PROGRAMADA,
            creado_por=self.gestor,
            asignado_a=self.gestor,
            enviar_correo=True,
        )
        actividad.documentos.add(self.doc)
        acuerdo = Acuerdo.objects.create(
            actividad=actividad,
            empresa=self.empresa,
            titulo="Enviar DOC-01",
            descripcion="Subir evidencia firmada",
            fecha_compromiso=timezone.now() + timedelta(days=3),
            estado=EstadoAcuerdo.ABIERTO,
            creado_por=self.gestor,
        )
        acuerdo.documentos.add(self.doc)
        self.assertEqual(actividad.acuerdos.count(), 1)
        self.assertTrue(acuerdo.documentos.filter(pk=self.doc.pk).exists())
        self.assertEqual(actividad.responsable_id, responsable.pk)
        self.assertEqual(actividad.area_id, self.area.pk)

        self.client.force_login(self.gestor)
        resp = self.client.get(
            reverse("maestro:actividad_crear"),
            {"empresa": self.empresa.pk, "area": self.area.pk},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Área responsable")
        self.assertContains(resp, self.area.nombre)
        self.assertContains(resp, "Ana Pérez")

        self.client.force_login(self.admin)
        resp_areas = self.client.get(reverse("maestro:empresa_areas", args=[self.empresa.pk]))
        self.assertEqual(resp_areas.status_code, 200)
        self.assertContains(resp_areas, "Áreas y responsables")
        self.assertContains(resp_areas, self.area.nombre)

    def test_actividad_pasa_a_pasada_tras_fecha_fin(self):
        from .models import sincronizar_actividades_pasadas

        actividad = Actividad.objects.create(
            titulo="Vencida",
            tipo=TipoActividad.GENERAL,
            alcance=AlcanceActividad.PERSONAL,
            inicio=timezone.now() - timedelta(days=3),
            fin=timezone.now() - timedelta(days=2),
            estado=EstadoActividad.PROGRAMADA,
            creado_por=self.gestor,
            asignado_a=self.gestor,
        )
        ids = sincronizar_actividades_pasadas(creado_por_id=self.gestor.pk)
        self.assertIn(actividad.pk, ids)
        actividad.refresh_from_db()
        self.assertEqual(actividad.estado, EstadoActividad.PASADA)

    def test_aviso_actividad_proxima_un_dia_antes(self):
        from .actividad_recordatorios import generar_avisos_actividades_proximas
        from .models import TipoAvisoConsultor

        manana = timezone.localtime(timezone.now()) + timedelta(days=1)
        actividad = Actividad.objects.create(
            titulo="Casi vence",
            tipo=TipoActividad.GENERAL,
            alcance=AlcanceActividad.CON_CLIENTE,
            empresa=self.empresa,
            inicio=manana,
            fin=manana,
            estado=EstadoActividad.PROGRAMADA,
            creado_por=self.gestor,
            asignado_a=self.gestor,
        )
        res = generar_avisos_actividades_proximas(dia=timezone.localdate())
        self.assertGreaterEqual(res["creados"], 1)
        self.assertTrue(
            AvisoConsultor.objects.filter(
                destinatario=self.gestor,
                tipo=TipoAvisoConsultor.ACTIVIDAD_PROXIMA,
                url__contains=f"/actividades/{actividad.pk}/",
            ).exists()
        )
        # No duplicar el mismo umbral
        res2 = generar_avisos_actividades_proximas(dia=timezone.localdate())
        self.assertEqual(res2["creados"], 0)

    def test_recordatorio_manual_varios_responsables_to_cc(self):
        from django.core import mail

        from .models import Responsable

        area2 = Area.objects.create(empresa=self.empresa, nombre="Ambiental")
        r1 = Responsable.objects.create(
            area=self.area,
            nombre="Ana Pérez",
            dni="12345678",
            correo="ana@example.com",
        )
        r2 = Responsable.objects.create(
            area=area2,
            nombre="Luis Gómez",
            dni="87654321",
            correo="luis@example.com",
        )
        actividad = Actividad.objects.create(
            titulo="Seguimiento correo",
            tipo=TipoActividad.GENERAL,
            alcance=AlcanceActividad.CON_CLIENTE,
            empresa=self.empresa,
            inicio=timezone.now() + timedelta(days=2),
            estado=EstadoActividad.PROGRAMADA,
            creado_por=self.gestor,
            asignado_a=self.gestor,
        )
        acuerdo = Acuerdo.objects.create(
            actividad=actividad,
            empresa=self.empresa,
            titulo="Compromiso texto",
            descripcion="Avance semanal",
            fecha_compromiso=timezone.now() + timedelta(days=5),
            estado=EstadoAcuerdo.ABIERTO,
            creado_por=self.gestor,
        )

        self.client.force_login(self.gestor)
        detalle = self.client.get(reverse("maestro:actividad_detalle", args=[actividad.pk]))
        self.assertEqual(detalle.status_code, 200)
        self.assertContains(detalle, "Destinatarios (responsables)")
        self.assertContains(detalle, "Ana Pérez")
        self.assertContains(detalle, "Luis Gómez")
        self.assertContains(detalle, "Operaciones")
        self.assertContains(detalle, "Ambiental")

        bandeja = self.client.get(reverse("maestro:acuerdo_bandeja"))
        self.assertEqual(bandeja.status_code, 200)
        self.assertContains(bandeja, "ana@example.com")
        self.assertContains(bandeja, "luis@example.com")

        mail.outbox.clear()
        resp = self.client.post(
            reverse("maestro:acuerdo_enviar_recordatorio", args=[acuerdo.pk]),
            {
                "responsables": [r1.pk, r2.pk],
                "mensaje": "Favor confirmar avance.",
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        enviado = mail.outbox[0]
        todos = sorted(enviado.to + list(enviado.cc or []))
        self.assertEqual(todos, ["ana@example.com", "luis@example.com"])
        self.assertEqual(len(enviado.to), 1)
        self.assertEqual(len(enviado.cc or []), 1)
        self.assertIn("Favor confirmar avance.", enviado.body)
        acuerdo.refresh_from_db()
        self.assertIsNotNone(acuerdo.ultimo_recordatorio_en)


@override_settings(
    AZURE_AD_REQUIRE_LOGIN=False,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
class EmpresaNotificacionesTests(FixturesMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client = Client()
        self.actividad = Actividad.objects.create(
            titulo="Act notif",
            tipo=TipoActividad.GENERAL,
            alcance=AlcanceActividad.CON_CLIENTE,
            empresa=self.empresa,
            inicio=timezone.now() + timedelta(days=10),
            estado=EstadoActividad.PROGRAMADA,
            creado_por=self.gestor,
            asignado_a=self.gestor,
        )
        # Usuario cliente con correo para envíos automáticos
        self.cliente.email = "cliente@empresa.test"
        self.cliente.save(update_fields=["email"])

    def _acuerdo(self, *, compromiso):
        return Acuerdo.objects.create(
            actividad=self.actividad,
            empresa=self.empresa,
            titulo="Acuerdo notif",
            descripcion="Detalle",
            fecha_compromiso=compromiso,
            estado=EstadoAcuerdo.ABIERTO,
            creado_por=self.gestor,
        )

    def test_ui_configuracion_reglas(self):
        self.client.force_login(self.admin)
        url = reverse("maestro:empresa_notificaciones", args=[self.empresa.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Notificaciones de acuerdos")
        self.assertNotContains(resp, "Plantilla del correo")
        self.assertContains(resp, "día 0")
        self.assertContains(resp, "Administración → Correos")

        resp_post = self.client.post(
            url,
            {
                "accion": "crear_regla",
                "tipo": TipoNotificacionAcuerdo.ANTES,
                "dias": "2, 5",
                "activo": "on",
            },
        )
        self.assertEqual(resp_post.status_code, 302)
        self.assertEqual(
            EmpresaNotificacionRegla.objects.filter(
                empresa=self.empresa,
                tipo=TipoNotificacionAcuerdo.ANTES,
            ).count(),
            2,
        )
        self.assertTrue(
            EmpresaNotificacionRegla.objects.filter(
                empresa=self.empresa,
                tipo=TipoNotificacionAcuerdo.ANTES,
                dias=2,
            ).exists()
        )

        # Día 0 reservado por el sistema
        resp_cero = self.client.post(
            url,
            {
                "accion": "crear_regla",
                "tipo": TipoNotificacionAcuerdo.ANTES,
                "dias": "0",
                "activo": "on",
            },
        )
        self.assertEqual(resp_cero.status_code, 200)
        self.assertContains(resp_cero, "configuración base del sistema")
        self.assertFalse(
            EmpresaNotificacionRegla.objects.filter(
                empresa=self.empresa, dias=0
            ).exists()
        )

    def test_regla_antes_dispara_en_dia_exacto(self):
        from django.core import mail

        from maestro.acuerdo_recordatorios import enviar_recordatorios_automaticos

        EmpresaNotificacionRegla.objects.create(
            empresa=self.empresa,
            tipo=TipoNotificacionAcuerdo.ANTES,
            dias=5,
            activo=True,
        )
        from datetime import datetime as dt

        hoy = timezone.localdate()
        compromiso = timezone.make_aware(dt.combine(hoy + timedelta(days=5), dt.min.time()))
        acuerdo = self._acuerdo(compromiso=compromiso)

        mail.outbox.clear()
        res = enviar_recordatorios_automaticos(dia=hoy)
        self.assertEqual(res["enviados"], 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("text/html", mail.outbox[0].alternatives[0][1])
        self.assertTrue(
            AcuerdoNotificacionEnvio.objects.filter(
                acuerdo=acuerdo,
                tipo=TipoNotificacionAcuerdo.ANTES,
                dias=5,
                dia_referencia=hoy,
            ).exists()
        )
        # No reenvía el mismo día
        mail.outbox.clear()
        res2 = enviar_recordatorios_automaticos(dia=hoy)
        self.assertEqual(res2["enviados"], 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_regla_post_dia_exacto(self):
        from datetime import datetime as dt

        from django.core import mail

        from maestro.acuerdo_recordatorios import enviar_recordatorios_automaticos

        EmpresaNotificacionRegla.objects.create(
            empresa=self.empresa,
            tipo=TipoNotificacionAcuerdo.POST,
            dias=7,
            activo=True,
        )
        hoy = timezone.localdate()
        compromiso = timezone.make_aware(dt.combine(hoy - timedelta(days=7), dt.min.time()))
        acuerdo = self._acuerdo(compromiso=compromiso)

        mail.outbox.clear()
        res = enviar_recordatorios_automaticos(dia=hoy)
        self.assertEqual(res["enviados"], 1)
        self.assertTrue(
            AcuerdoNotificacionEnvio.objects.filter(
                acuerdo=acuerdo,
                tipo=TipoNotificacionAcuerdo.POST,
                dias=7,
            ).exists()
        )
        # Mismo día: no reenvía
        mail.outbox.clear()
        self.assertEqual(enviar_recordatorios_automaticos(dia=hoy)["enviados"], 0)
        # Otro día (no es el N exacto): no
        self.assertEqual(
            enviar_recordatorios_automaticos(dia=hoy + timedelta(days=3))["enviados"],
            0,
        )
        # Día 14 post (sin regla): no
        mail.outbox.clear()
        self.assertEqual(
            enviar_recordatorios_automaticos(dia=hoy + timedelta(days=7))["enviados"],
            0,
        )


@override_settings(
    AZURE_AD_REQUIRE_LOGIN=False,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
class JerarquiaActividadTests(FixturesMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client = Client()
        self.jefe = _user("jefe@test.local", first_name="Juan", last_name="Jefe")
        _perfil(self.jefe, TipoUsuario.GESTOR, empresas=[self.empresa])
        self.consultor = _user("consultor@test.local", first_name="Carla", last_name="Cons")
        perfil_cons = _perfil(self.consultor, TipoUsuario.GESTOR, empresas=[self.empresa])
        perfil_cons.reporta_a = self.jefe
        perfil_cons.save(update_fields=["reporta_a", "actualizado_en"])

    def test_jefe_ve_actividad_de_subordinado(self):
        from .permissions import actividades_visibles, puede_ver_actividad

        act = Actividad.objects.create(
            titulo="Del consultor",
            tipo=TipoActividad.GENERAL,
            alcance=AlcanceActividad.PERSONAL,
            inicio=timezone.now() + timedelta(days=2),
            estado=EstadoActividad.PROGRAMADA,
            creado_por=self.consultor,
            asignado_a=self.consultor,
        )
        self.assertTrue(puede_ver_actividad(self.jefe, act))
        self.assertTrue(puede_ver_actividad(self.consultor, act))
        self.assertTrue(puede_ver_actividad(self.admin, act))
        self.assertIn(act, actividades_visibles(self.jefe))
        # El consultor no ve actividades solo del jefe
        act_jefe = Actividad.objects.create(
            titulo="Del jefe",
            tipo=TipoActividad.GENERAL,
            alcance=AlcanceActividad.PERSONAL,
            inicio=timezone.now() + timedelta(days=2),
            estado=EstadoActividad.PROGRAMADA,
            creado_por=self.jefe,
            asignado_a=self.jefe,
        )
        self.assertFalse(puede_ver_actividad(self.consultor, act_jefe))
        self.assertTrue(puede_ver_actividad(self.jefe, act_jefe))

    def test_asignar_actividad_a_subordinado(self):
        self.client.force_login(self.jefe)
        inicio = (timezone.localtime(timezone.now()) + timedelta(days=1)).strftime(
            "%Y-%m-%dT%H:%M"
        )
        resp = self.client.post(
            reverse("maestro:actividad_crear"),
            {
                "titulo": "Asignada al equipo",
                "descripcion": "",
                "alcance": AlcanceActividad.PERSONAL,
                "empresa": "",
                "asignado_a": self.consultor.pk,
                "area": "",
                "responsable": "",
                "inicio": inicio,
                "fin": "",
                "enlace_reunion": "",
            },
        )
        self.assertEqual(resp.status_code, 302)
        act = Actividad.objects.get(titulo="Asignada al equipo")
        self.assertEqual(act.creado_por_id, self.jefe.pk)
        self.assertEqual(act.asignado_a_id, self.consultor.pk)

    def test_aviso_proxima_llega_al_asignado(self):
        from .actividad_recordatorios import generar_avisos_actividades_proximas

        manana = timezone.localtime(timezone.now()) + timedelta(days=1)
        act = Actividad.objects.create(
            titulo="Asignada vence",
            tipo=TipoActividad.GENERAL,
            alcance=AlcanceActividad.PERSONAL,
            inicio=manana,
            fin=manana,
            estado=EstadoActividad.PROGRAMADA,
            creado_por=self.jefe,
            asignado_a=self.consultor,
        )
        res = generar_avisos_actividades_proximas(dia=timezone.localdate())
        self.assertGreaterEqual(res["creados"], 2)  # asignado + creador
        self.assertTrue(
            AvisoConsultor.objects.filter(
                destinatario=self.consultor,
                tipo=TipoAvisoConsultor.ACTIVIDAD_PROXIMA,
                url__contains=f"/actividades/{act.pk}/",
            ).exists()
        )
        self.assertTrue(
            AvisoConsultor.objects.filter(
                destinatario=self.jefe,
                tipo=TipoAvisoConsultor.ACTIVIDAD_PROXIMA,
                url__contains=f"/actividades/{act.pk}/",
            ).exists()
        )


@override_settings(
    AZURE_AD_REQUIRE_LOGIN=False,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
class EmpresaTamanoArchivoTests(FixturesMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client = Client()

    def test_crear_empresa_con_tamano_max(self):
        self.client.force_login(self.admin)
        resp = self.client.post(
            reverse("maestro:empresa_create"),
            {
                "nombre": "Minera Límite",
                "ruc": "20999999999",
                "tamano_max_mb": "10",
                "activo": "on",
            },
        )
        self.assertEqual(resp.status_code, 302)
        empresa = Empresa.objects.get(ruc="20999999999")
        self.assertEqual(empresa.tamano_max_mb, 10)

    def test_carga_rechaza_archivo_que_supera_limite(self):
        from maestro.forms import DocumentoCargaForm

        self.empresa.tamano_max_mb = 1
        self.empresa.save(update_fields=["tamano_max_mb", "actualizado_en"])
        # ~1.5 MB
        grande = SimpleUploadedFile(
            "evidencia.pdf",
            b"x" * (1024 * 1024 + 512 * 1024),
            content_type="application/pdf",
        )
        form = DocumentoCargaForm(
            data={"observaciones": ""},
            files={"archivo": grande},
            empresa=self.empresa,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("archivo", form.errors)
        self.assertIn("supera el máximo", form.errors["archivo"][0])


@override_settings(
    AZURE_AD_REQUIRE_LOGIN=False,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
class PlanEntregaTests(FixturesMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client = Client()

    def test_guardar_fecha_limite_por_documento(self):
        from datetime import date

        self.client.force_login(self.admin)
        url = reverse(
            "maestro:empresa_revision_plan",
            args=[self.empresa.pk, self.revision.pk],
        )
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Definir plan de entrega")
        self.assertContains(resp, self.doc.codigo)

        resp_post = self.client.post(
            url,
            {f"fecha_limite_{self.doc.pk}": "2026-06-15"},
        )
        self.assertEqual(resp_post.status_code, 302)
        self.doc.refresh_from_db()
        self.assertEqual(self.doc.fecha_limite_entrega, date(2026, 6, 15))


@override_settings(
    AZURE_AD_REQUIRE_LOGIN=False,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
class DocumentoRevisionConsultorTests(FixturesMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client = Client()

    def test_definir_revision_y_porcentaje(self):
        self.client.force_login(self.gestor)
        url = reverse(
            "maestro:seguimiento_documento_revision_consultor",
            args=[self.empresa.pk, self.doc.pk],
        )
        resp = self.client.post(
            url,
            {
                "revision_texto": "Rev0",
                "revision_porcentaje": "35",
                "next": reverse("maestro:seguimiento_empresa_revision", args=[self.empresa.pk]),
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.doc.refresh_from_db()
        self.assertEqual(self.doc.revision_texto, "Rev0")
        self.assertEqual(self.doc.revision_porcentaje, 35)

        progreso = calcular_progreso_revision(self.revision)
        item = next(d for d in progreso.documentos if d.id == self.doc.pk)
        self.assertEqual(item.revision_texto, "Rev0")
        self.assertEqual(item.revision_porcentaje, 35)
        from maestro.indicadores import _estado_madurez_doc

        self.assertEqual(_estado_madurez_doc(item), "Rev0")


@override_settings(
    AZURE_AD_REQUIRE_LOGIN=False,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
class ViewsSmokeTests(FixturesMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client = Client()

    def test_login_publico(self):
        resp = self.client.get(reverse("maestro:login"))
        self.assertEqual(resp.status_code, 200)

    def test_gestor_seguimiento_y_operacion(self):
        self.client.force_login(self.gestor)
        urls = [
            reverse("maestro:home"),
            reverse("maestro:seguimiento_list"),
            reverse("maestro:seguimiento_empresa_detail", args=[self.empresa.pk]),
            reverse("maestro:seguimiento_empresa_monitoreo", args=[self.empresa.pk]),
            reverse("maestro:seguimiento_empresa_revision", args=[self.empresa.pk]),
            reverse("maestro:seguimiento_empresa_pendientes", args=[self.empresa.pk]),
            reverse("maestro:actividad_calendario"),
            reverse("maestro:acuerdo_bandeja"),
            reverse("maestro:aviso_list"),
        ]
        for url in urls:
            with self.subTest(url=url):
                resp = self.client.get(url)
                self.assertEqual(resp.status_code, 200, msg=url)

    def test_cliente_portal(self):
        self.client.force_login(self.cliente)
        for url in [
            reverse("maestro:home"),
            reverse("maestro:mi_empresa_documentos"),
            reverse("maestro:actividad_calendario"),
            reverse("maestro:acuerdo_bandeja"),
        ]:
            with self.subTest(url=url):
                resp = self.client.get(url)
                self.assertEqual(resp.status_code, 200, msg=url)
        # Avance redirige al dashboard de indicadores de su empresa
        resp = self.client.get(reverse("maestro:mi_empresa_dashboard"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(
            reverse("maestro:indicadores_empresa", args=[self.empresa.pk]),
            resp.url,
        )

    def test_cliente_no_accede_a_seguimiento(self):
        self.client.force_login(self.cliente)
        resp = self.client.get(reverse("maestro:seguimiento_list"))
        self.assertEqual(resp.status_code, 302)

    def test_cliente_carga_evidencia_por_url(self):
        self.client.force_login(self.cliente)
        url = reverse("maestro:mi_empresa_documento_cargar", args=[self.doc.pk])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "URL / enlace")
        resp = self.client.post(
            url,
            {
                "tipo_evidencia": "url",
                "url_evidencia": "https://example.com/evidencia-gistm.pdf",
                "observaciones": "Enlace SharePoint",
            },
        )
        self.assertEqual(resp.status_code, 302)
        carga = DocumentoCarga.objects.filter(
            empresa_documento=self.doc, activo=True
        ).first()
        self.assertIsNotNone(carga)
        self.assertTrue(carga.es_url)
        self.assertEqual(
            carga.url_evidencia, "https://example.com/evidencia-gistm.pdf"
        )
        self.assertFalse(bool(carga.archivo))
        self.assertEqual(carga.evidencia_url, "https://example.com/evidencia-gistm.pdf")

    def test_cliente_lectura_no_puede_cargar(self):
        lectura = _user("lectura@test.local", first_name="Luz", last_name="Lectura")
        _perfil(
            lectura,
            TipoUsuario.CLIENTE_LECTURA,
            empresa=self.empresa,
            revisiones=[self.revision],
        )
        self.client.force_login(lectura)
        resp = self.client.get(reverse("maestro:mi_empresa_documentos"))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Cargar")
        resp = self.client.get(
            reverse("maestro:mi_empresa_documento_cargar", args=[self.doc.pk])
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn(
            reverse("maestro:mi_empresa_documentos"),
            resp.url,
        )

    def test_cliente_solo_ve_revisiones_asignadas(self):
        otra_rev = Revision.objects.create(
            empresa=self.empresa, anio=2025, nombre="R2025", activo=True
        )
        # Solo asignada la revisión 2026
        self.perfil_cliente.revisiones.set([self.revision])
        self.client.force_login(self.cliente)
        resp = self.client.get(
            reverse("maestro:indicadores_empresa", args=[self.empresa.pk]),
            {"rev": otra_rev.pk},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["revision"].pk, self.revision.pk)
        self.assertNotIn(
            otra_rev,
            resp.context["revisiones_activas"],
        )

    def test_empresa_delete_solo_desactiva(self):
        pk = self.empresa.pk
        self.empresa.delete()
        empresa = Empresa.objects.get(pk=pk)
        self.assertFalse(empresa.activo)
        self.assertTrue(Revision.objects.filter(empresa_id=pk).exists())

    def test_auditoria_carga_y_listado(self):
        from maestro.models import RegistroAuditoria, TipoAuditoria

        self.client.force_login(self.cliente)
        url = reverse("maestro:mi_empresa_documento_cargar", args=[self.doc.pk])
        resp = self.client.post(
            url,
            {
                "tipo_evidencia": "url",
                "url_evidencia": "https://example.com/audit-ev.pdf",
                "observaciones": "",
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(
            RegistroAuditoria.objects.filter(
                tipo=TipoAuditoria.CARGA, usuario=self.cliente
            ).exists()
        )
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("maestro:auditoria_list"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Carga de evidencia")

    def _completar_vigencia_aprobacion(self, doc):
        """Paso obligatorio tras aprobar: guardar vigencia confirma la aprobación."""
        hoy = timezone.localdate()
        url_vig = reverse(
            "maestro:seguimiento_documento_vigencia",
            args=[self.empresa.pk, doc.pk],
        )
        resp = self.client.post(
            url_vig,
            {
                "frecuencia_actualizacion": "ANUAL",
                "fecha_ultima_revision": hoy.isoformat(),
                "fecha_caducidad": (hoy + timedelta(days=365)).isoformat(),
            },
        )
        self.assertEqual(resp.status_code, 302)
        return resp

    def test_aprobar_carga_desde_seguimiento(self):
        carga = DocumentoCarga.objects.create(
            empresa_documento=self.doc,
            archivo=SimpleUploadedFile("ev.pdf", b"%PDF-1.4 demo"),
            subido_por=self.cliente,
            activo=True,
            estado_revision=EstadoRevisionCarga.PENDIENTE,
        )
        self.client.force_login(self.gestor)
        url = reverse(
            "maestro:seguimiento_carga_aprobar",
            args=[self.empresa.pk, carga.pk],
        )
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 302)
        # Sin vigencia aún no se confirma la aprobación
        carga.refresh_from_db()
        self.assertEqual(carga.estado_revision, EstadoRevisionCarga.PENDIENTE)
        self.assertIn(
            reverse(
                "maestro:seguimiento_documento_vigencia",
                args=[self.empresa.pk, self.doc.pk],
            ),
            resp.url,
        )
        self._completar_vigencia_aprobacion(self.doc)
        carga.refresh_from_db()
        self.assertEqual(carga.estado_revision, EstadoRevisionCarga.APROBADO)
        self.assertEqual(carga.revisado_por_id, self.gestor.pk)
        self.assertIsNotNone(carga.revisado_en)
        self.vinculo.refresh_from_db()
        self.assertEqual(self.vinculo.estado_revision, EstadoRevisionCriterio.APROBADO)

        # Revertir aprobación (error de usuario)
        url_rev = reverse(
            "maestro:seguimiento_carga_revertir",
            args=[self.empresa.pk, carga.pk],
        )
        resp = self.client.post(url_rev)
        self.assertEqual(resp.status_code, 302)
        carga.refresh_from_db()
        self.assertEqual(carga.estado_revision, EstadoRevisionCarga.PENDIENTE)
        self.vinculo.refresh_from_db()
        self.assertEqual(self.vinculo.estado_revision, EstadoRevisionCriterio.PENDIENTE)

    def test_revision_parcial_con_observacion_por_pregunta(self):
        c2 = Criterio.objects.create(
            codigo="C2",
            descripcion="Criterio 2",
            requisito=self.requisito,
        )
        q2 = Pregunta.objects.create(
            codigo="Q-C2", texto="Pregunta de C2", criterio=c2, orden=1
        )
        v2 = EmpresaDocumentoCriterio.objects.create(
            revision=self.revision,
            empresa_documento=self.doc,
            criterio=c2,
            area=self.area,
        )
        r2 = EmpresaDocumentoPregunta.objects.get(vinculo_criterio=v2, pregunta=q2)
        carga = DocumentoCarga.objects.create(
            empresa_documento=self.doc,
            archivo=SimpleUploadedFile("ev.pdf", b"%PDF-1.4 demo"),
            subido_por=self.cliente,
            activo=True,
            estado_revision=EstadoRevisionCarga.PENDIENTE,
        )
        self.client.force_login(self.gestor)
        url = reverse(
            "maestro:seguimiento_carga_revisar",
            args=[self.empresa.pk, carga.pk],
        )
        resp = self.client.post(
            url,
            {
                "accion": "guardar",
                f"accion_p_{self.respuesta.pk}": "CUMPLE",
                f"accion_p_{r2.pk}": "NO_CUMPLE",
                f"obs_p_{r2.pk}": "Falta el anexo de monitoreo trimestral.",
            },
        )
        self.assertEqual(resp.status_code, 302)
        carga.refresh_from_db()
        self.assertEqual(carga.estado_revision, EstadoRevisionCarga.PARCIAL)
        self.vinculo.refresh_from_db()
        v2.refresh_from_db()
        self.assertEqual(self.vinculo.estado_revision, EstadoRevisionCriterio.APROBADO)
        self.assertEqual(v2.estado_revision, EstadoRevisionCriterio.OBSERVADO)
        msg = DocumentoMensaje.objects.filter(
            empresa_documento=self.doc,
            criterio=c2,
            tipo=TipoDocumentoMensaje.OBSERVACION,
        ).first()
        self.assertIsNotNone(msg)
        self.assertIn("monitoreo", msg.texto)

        # Cerrar el restante → pide vigencia antes de aprobar
        resp = self.client.post(
            url,
            {
                "accion": "guardar",
                f"accion_p_{r2.pk}": "CUMPLE",
            },
        )
        self.assertEqual(resp.status_code, 302)
        carga.refresh_from_db()
        self.assertEqual(carga.estado_revision, EstadoRevisionCarga.PARCIAL)
        self.assertIn(
            reverse(
                "maestro:seguimiento_documento_vigencia",
                args=[self.empresa.pk, self.doc.pk],
            ),
            resp.url,
        )
        self._completar_vigencia_aprobacion(self.doc)
        carga.refresh_from_db()
        self.assertEqual(carga.estado_revision, EstadoRevisionCarga.APROBADO)

    def test_aviso_marcar_leido(self):
        aviso = AvisoConsultor.objects.create(
            destinatario=self.gestor,
            empresa=self.empresa,
            tipo=TipoAvisoConsultor.MENSAJE,
            titulo="Demo",
            detalle="Hola",
            url=reverse("maestro:seguimiento_empresa_revision", args=[self.empresa.pk]),
        )
        self.client.force_login(self.gestor)
        resp = self.client.post(reverse("maestro:aviso_marcar_leido", args=[aviso.pk]))
        self.assertEqual(resp.status_code, 302)
        aviso.refresh_from_db()
        self.assertTrue(aviso.leido)


@override_settings(
    AZURE_AD_REQUIRE_LOGIN=False,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
class IndicadoresTests(FixturesMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client = Client()
        self.doc_propio = EmpresaDocumento.objects.create(
            revision=self.revision,
            codigo="DOC-PROPIO",
            nombre="Documento cliente",
            tipo_documento="PDF",
            origen=OrigenEmpresaDocumento.PROPIO,
            activo=True,
        )

    def test_cliente_accede_a_indicadores_de_su_empresa(self):
        self.client.force_login(self.cliente)
        resp = self.client.get(reverse("maestro:indicadores_list"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn(
            reverse("maestro:indicadores_empresa", args=[self.empresa.pk]),
            resp.url,
        )
        for tab in ("general", "progreso", "documentos", "estandar", "glosario"):
            with self.subTest(tab=tab):
                url = reverse("maestro:indicadores_empresa", args=[self.empresa.pk])
                resp = self.client.get(url, {"tab": tab})
                self.assertEqual(resp.status_code, 200, msg=tab)
        # No puede ver otra empresa
        resp = self.client.get(
            reverse("maestro:indicadores_empresa", args=[self.otra_empresa.pk])
        )
        self.assertEqual(resp.status_code, 302)

    def test_gestor_lista_y_dashboard_tabs(self):
        self.client.force_login(self.gestor)
        resp = self.client.get(reverse("maestro:indicadores_list"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, self.empresa.nombre)
        self.assertNotContains(resp, self.otra_empresa.nombre)

        for tab in ("general", "progreso", "documentos", "estandar", "glosario"):
            with self.subTest(tab=tab):
                url = reverse("maestro:indicadores_empresa", args=[self.empresa.pk])
                resp = self.client.get(url, {"tab": tab})
                self.assertEqual(resp.status_code, 200, msg=tab)

    def test_gestor_no_ve_empresa_ajena(self):
        self.client.force_login(self.gestor)
        resp = self.client.get(
            reverse("maestro:indicadores_empresa", args=[self.otra_empresa.pk])
        )
        self.assertEqual(resp.status_code, 302)

    def test_clasificacion_referencial_vs_multidisciplinario(self):
        progreso = calcular_progreso_revision(self.revision)
        docs = documentos_indicadores(self.revision, progreso, empresa=self.empresa)
        refs = filtrar_documentos(docs, "referenciales")
        multi = filtrar_documentos(docs, "multidisciplinario")
        self.assertTrue(any(d.codigo == "DOC-01" for d in refs))
        self.assertTrue(any(d.codigo == "DOC-PROPIO" for d in multi))
        self.assertFalse(any(d.codigo == "DOC-PROPIO" for d in refs))

    def test_documentos_multiprincipio(self):
        principio2 = Principio.objects.create(
            codigo="P2", descripcion="Principio 2", tema=self.tema
        )
        requisito2 = Requisito.objects.create(
            codigo="R2", descripcion="Requisito 2", principio=principio2
        )
        c2 = Criterio.objects.create(
            codigo="C2",
            descripcion="Criterio otro principio",
            requisito=requisito2,
        )
        EmpresaDocumentoCriterio.objects.create(
            revision=self.revision,
            empresa_documento=self.doc,
            criterio=c2,
            area=self.area,
        )
        progreso = calcular_progreso_revision(self.revision)
        docs = documentos_indicadores(self.revision, progreso, empresa=self.empresa)
        multi_p = filtrar_documentos(docs, "multiprincipio")
        self.assertTrue(any(d.codigo == "DOC-01" and d.es_multiprincipio for d in docs))
        self.assertTrue(any(d.codigo == "DOC-01" for d in multi_p))
        self.assertFalse(any(d.codigo == "DOC-PROPIO" for d in multi_p))

        self.client.force_login(self.gestor)
        url = reverse("maestro:indicadores_empresa", args=[self.empresa.pk])
        resp = self.client.get(url, {"tab": "documentos", "docs": "multiprincipio"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Multiprincipio")
        self.assertContains(resp, "DOC-01")

    def test_avance_por_principios_con_aprobado(self):
        DocumentoCarga.objects.create(
            empresa_documento=self.doc,
            archivo=SimpleUploadedFile("ok.pdf", b"%PDF-1.4 ok"),
            subido_por=self.cliente,
            activo=True,
            estado_revision=EstadoRevisionCarga.APROBADO,
            revisado_por=self.gestor,
            revisado_en=timezone.now(),
        )
        self._aprobar_criterio_via_preguntas()
        progreso = calcular_progreso_revision(self.revision)
        principios = avance_por_principios(progreso)
        self.assertEqual(len(principios), 1)
        self.assertEqual(principios[0].codigo, "P1")
        self.assertEqual(principios[0].pct, 100.0)

        board = tablero_progreso(progreso, empresa=self.empresa)
        self.assertTrue(board["temas"])
        self.assertEqual(board["temas"][0]["principios"][0]["pct"], 100.0)
        self.assertTrue(board["temas"][0]["requisitos"])
        self.assertTrue(board["temas"][0]["criterios"])
        self.assertTrue(board["temas"][0]["documentos"])

        est = tablero_estandar()
        self.assertTrue(est["temas"])
        self.assertTrue(est["temas"][0]["principios"])
        self.assertIn("descripcion", est["temas"][0]["principios"][0])
        self.assertTrue(est["temas"][0]["principios"][0]["requisitos"])

    def test_curva_y_pastel_y_prioritarios(self):
        from datetime import date

        self.revision.fecha_inicio = date(self.revision.anio, 1, 1)
        self.revision.fecha_fin = date(self.revision.anio, 12, 31)
        self.revision.save(update_fields=["fecha_inicio", "fecha_fin"])

        DocumentoCarga.objects.create(
            empresa_documento=self.doc,
            archivo=SimpleUploadedFile("ok.pdf", b"%PDF-1.4 ok"),
            subido_por=self.cliente,
            activo=True,
            estado_revision=EstadoRevisionCarga.APROBADO,
            revisado_por=self.gestor,
            revisado_en=timezone.now(),
        )
        self._aprobar_criterio_via_preguntas()
        progreso = calcular_progreso_revision(self.revision)
        curva = curva_avance(self.revision, progreso)
        self.assertFalse(curva.get("sin_fechas"))
        self.assertEqual(len(curva["labels"]), 12)
        self.assertTrue(any(v is not None for v in curva["real"]))
        self.assertTrue(any(v is not None for v in curva["forecast"]))

        sin_fechas = curva_avance(
            Revision.objects.create(
                empresa=self.empresa, anio=2099, nombre="sin fechas", activo=False
            ),
            progreso,
        )
        self.assertTrue(sin_fechas.get("sin_fechas"))

        self.doc.revision_texto = "RevB"
        self.doc.revision_porcentaje = 80
        self.doc.save(update_fields=["revision_texto", "revision_porcentaje", "actualizado_en"])
        progreso = calcular_progreso_revision(self.revision)
        pastel = chart_documentos_estados(progreso)
        self.assertIn("RevB", pastel["labels"])
        self.assertGreaterEqual(pastel["values"][pastel["labels"].index("RevB")], 1)

        temas_chart = chart_temas(progreso)
        self.assertTrue(temas_chart["labels"])
        self.assertEqual(len(temas_chart["labels"]), len(temas_chart["values"]))
        self.assertTrue(all(0 <= v <= 100 for v in temas_chart["values"]))
        self.assertGreater(max(temas_chart["values"]), 0)

        self.doc_propio.prioritario = True
        self.doc_propio.save(update_fields=["prioritario"])
        prio = documentos_prioritarios(self.revision, progreso)
        self.assertTrue(any(d.codigo == "DOC-PROPIO" for d in prio))
        self.doc_propio.prioritario = False
        self.doc_propio.save(update_fields=["prioritario"])
        self.assertFalse(
            any(d.codigo == "DOC-PROPIO" for d in documentos_prioritarios(self.revision, progreso))
        )

        self.client.force_login(self.gestor)
        resp = self.client.get(
            reverse("maestro:indicadores_empresa", args=[self.empresa.pk]),
            {"tab": "general"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Curva avance")
        self.assertContains(resp, "Prioritarios")
        self.assertContains(resp, "chart-curva-avance")


@override_settings(
    AZURE_AD_REQUIRE_LOGIN=False,
    CSP_ENABLED=True,
    SECURE_SSL_REDIRECT=False,
)
class SecurityHeadersTests(TestCase):
    def test_csp_header_con_nonce_sin_unsafe_inline_scripts(self):
        client = Client()
        resp = client.get(reverse("maestro:login"))
        self.assertEqual(resp.status_code, 200)
        csp = resp.get("Content-Security-Policy", "")
        self.assertIn("default-src 'self'", csp)
        self.assertIn("object-src 'none'", csp)
        self.assertNotIn("cdn.jsdelivr.net", csp)
        self.assertNotIn("fonts.googleapis.com", csp)
        self.assertNotIn("fonts.gstatic.com", csp)
        self.assertIn("script-src 'self'", csp)
        self.assertIn("font-src 'self'", csp)
        self.assertContains(resp, "maestro/css/fonts.css")
        self.assertContains(resp, "Montserrat")
        self.assertRegex(csp, r"script-src[^;]*'nonce-[A-Za-z0-9_\-]+'")
        self.assertRegex(csp, r"style-src[^;]*'nonce-[A-Za-z0-9_\-]+'")
        # Sin unsafe-inline en script ni style
        for directive in ("script-src", "style-src"):
            parts = [p.strip() for p in csp.split(";") if p.strip().startswith(directive)]
            self.assertTrue(parts, msg=directive)
            self.assertNotIn("'unsafe-inline'", parts[0], msg=directive)
        self.assertContains(resp, 'nonce="')
        self.assertContains(resp, "csp_inline.css")
