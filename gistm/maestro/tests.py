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
    AlcanceActividad,
    Area,
    AvisoConsultor,
    Criterio,
    DocumentoCarga,
    Empresa,
    EmpresaDocumento,
    EmpresaDocumentoCriterio,
    EstadoAcuerdo,
    EstadoActividad,
    EstadoRevisionCarga,
    EstadoRevisionCriterio,
    DocumentoMensaje,
    OrigenEmpresaDocumento,
    TipoDocumentoMensaje,
    PerfilUsuario,
    Principio,
    Requisito,
    Revision,
    Tema,
    TipoActividad,
    TipoAvisoConsultor,
    TipoUsuario,
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


def _perfil(user, tipo: str, *, empresa=None, empresas=None) -> PerfilUsuario:
    perfil = PerfilUsuario.objects.create(
        user=user,
        tipo=tipo,
        activo=True,
        empresa=empresa if tipo == TipoUsuario.CLIENTE else None,
    )
    if tipo == TipoUsuario.GESTOR and empresas:
        perfil.empresas.set(empresas)
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

        self.admin = _user("admin@test.local", first_name="Ana", last_name="Admin")
        _perfil(self.admin, TipoUsuario.ADMINISTRADOR)

        self.gestor = _user("gestor@test.local", first_name="Gina", last_name="Gestor")
        _perfil(self.gestor, TipoUsuario.GESTOR, empresas=[self.empresa])

        self.cliente = _user("cliente@test.local", first_name="Carlos", last_name="Cliente")
        _perfil(self.cliente, TipoUsuario.CLIENTE, empresa=self.empresa)


@override_settings(
    AZURE_AD_REQUIRE_LOGIN=False,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
class ProgressTests(FixturesMixin, TestCase):
    def test_revision_activa(self):
        self.assertEqual(revision_activa_empresa(self.empresa.pk), self.revision)

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
        self.vinculo.estado_revision = EstadoRevisionCriterio.APROBADO
        self.vinculo.save(update_fields=["estado_revision", "actualizado_en"])
        progreso = calcular_progreso_revision(self.revision)
        self.assertEqual(progreso.docs_cargados, 1)
        self.assertEqual(progreso.docs_pct, 100.0)
        self.assertEqual(progreso.criterios_completos, 1)

    def test_aprobacion_parcial_suma_criterio_no_documento(self):
        c2 = Criterio.objects.create(
            codigo="C2",
            descripcion="Criterio 2",
            requisito=self.requisito,
        )
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
        self.vinculo.estado_revision = EstadoRevisionCriterio.APROBADO
        self.vinculo.save(update_fields=["estado_revision", "actualizado_en"])
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
        actividad = Actividad.objects.create(
            titulo="Reunión semanal",
            descripcion="Seguimiento de evidencias",
            tipo=TipoActividad.GENERAL,
            alcance=AlcanceActividad.CON_CLIENTE,
            empresa=self.empresa,
            inicio=timezone.now() + timedelta(days=1),
            estado=EstadoActividad.PROGRAMADA,
            creado_por=self.gestor,
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
            reverse("maestro:mi_empresa_dashboard"),
            reverse("maestro:mi_empresa_documentos"),
        ]:
            with self.subTest(url=url):
                resp = self.client.get(url)
                self.assertEqual(resp.status_code, 200, msg=url)

    def test_cliente_no_accede_a_seguimiento(self):
        self.client.force_login(self.cliente)
        resp = self.client.get(reverse("maestro:seguimiento_list"))
        self.assertEqual(resp.status_code, 302)

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
        carga.refresh_from_db()
        self.assertEqual(carga.estado_revision, EstadoRevisionCarga.APROBADO)
        self.assertEqual(carga.revisado_por_id, self.gestor.pk)
        self.assertIsNotNone(carga.revisado_en)
        self.vinculo.refresh_from_db()
        self.assertEqual(self.vinculo.estado_revision, EstadoRevisionCriterio.APROBADO)

    def test_revision_parcial_con_observacion_por_criterio(self):
        c2 = Criterio.objects.create(
            codigo="C2",
            descripcion="Criterio 2",
            requisito=self.requisito,
        )
        v2 = EmpresaDocumentoCriterio.objects.create(
            revision=self.revision,
            empresa_documento=self.doc,
            criterio=c2,
            area=self.area,
        )
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
                f"accion_v_{self.vinculo.pk}": "APROBADO",
                f"accion_v_{v2.pk}": "OBSERVADO",
                f"obs_v_{v2.pk}": "Falta el anexo de monitoreo trimestral.",
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

        # Cerrar el restante
        resp = self.client.post(
            url,
            {
                "accion": "guardar",
                f"accion_v_{v2.pk}": "APROBADO",
            },
        )
        self.assertEqual(resp.status_code, 302)
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

    def test_cliente_no_accede_a_indicadores(self):
        self.client.force_login(self.cliente)
        resp = self.client.get(reverse("maestro:indicadores_list"))
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
        self.vinculo.estado_revision = EstadoRevisionCriterio.APROBADO
        self.vinculo.save(update_fields=["estado_revision", "actualizado_en"])
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
        self.vinculo.estado_revision = EstadoRevisionCriterio.APROBADO
        self.vinculo.save(update_fields=["estado_revision", "actualizado_en"])
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
        self.assertContains(resp, "Documentos prioritarios")
        self.assertContains(resp, "chart-curva-avance")
