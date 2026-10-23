from __future__ import annotations

from datetime import date, datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from maestro.models import (
    Actividad,
    Acuerdo,
    AlcanceActividad,
    Area,
    DocumentoCarga,
    DocumentoCriterio,
    Empresa,
    EmpresaDocumento,
    EmpresaDocumentoCriterio,
    EmpresaDocumentoPregunta,
    EstadoActividad,
    EstadoAcuerdo,
    EstadoInstalacionRevision,
    EstadoRevisionCarga,
    EstadoRevisionPregunta,
    FaseInstalacionRevision,
    PerfilUsuario,
    Pregunta,
    Responsable,
    Revision,
    TipoActividad,
    TipoUsuario,
    aplicar_deshabilitados_catalogo_a_revision,
    aplicar_estandar_a_revision,
    sincronizar_actividades_pasadas,
    sincronizar_acuerdos_vencidos,
    sincronizar_estado_carga_documento,
    sincronizar_estado_criterio_desde_preguntas,
)
from maestro.progress import calcular_progreso_revision

User = get_user_model()

_MINI_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj\n"
    b"3 0 obj<</Type/Page/MediaBox[0 0 300 144]/Parent 2 0 R>>endobj\n"
    b"trailer<</Size 4/Root 1 0 R>>\n"
    b"%%EOF\n"
)
_TAG_OBS = "[demo-seed]"


class Command(BaseCommand):
    help = (
        "Crea 2 empresas de prueba con revisiones, aplica el estándar "
        "(misma lógica que la UI) y marca avance parcial en preguntas."
    )

    @transaction.atomic
    def handle(self, *args, **options):
        if not DocumentoCriterio.objects.exists():
            self.stderr.write(
                self.style.ERROR(
                    "No hay relación estándar. Ejecuta antes importar_nuevo_catalogo."
                )
            )
            return
        if Pregunta.objects.count() == 0:
            self.stderr.write(
                self.style.ERROR("No hay preguntas en catálogo. Importa nuevo.xlsx primero.")
            )
            return

        # --- Empresa 1: 2 revisiones ---
        emp1, _ = Empresa.objects.update_or_create(
            ruc="20601234567",
            defaults={
                "nombre": "Minera Andes Sur S.A.C.",
                "tamano_max_mb": 25,
                "activo": True,
            },
        )
        area1_ops, _ = Area.objects.update_or_create(
            empresa=emp1, nombre="Operaciones", defaults={"activo": True}
        )
        Area.objects.update_or_create(
            empresa=emp1, nombre="Sustentabilidad", defaults={"activo": True}
        )

        rev1a = self._crear_revision(
            emp1,
            anio=2026,
            nombre="Embalse N°1 · 2026",
            instalacion="Embalse N°1",
            estado=EstadoInstalacionRevision.EXISTENTE,
            fase=FaseInstalacionRevision.OPERACION,
            capacidad="45 Mm³",
            vida_util="2038",
            fecha_inicio=date(2026, 1, 1),
            fecha_fin=date(2026, 12, 31),
            area=area1_ops,
        )
        rev1b = self._crear_revision(
            emp1,
            anio=2025,
            nombre="Embalse N°1 · cierre parcial 2025",
            instalacion="Embalse N°1",
            estado=EstadoInstalacionRevision.EXISTENTE,
            fase=FaseInstalacionRevision.CIERRE,
            capacidad="45 Mm³",
            vida_util="2038",
            fecha_inicio=date(2025, 3, 1),
            fecha_fin=date(2025, 12, 15),
            area=area1_ops,
            activo=False,
        )

        # --- Empresa 2: 1 revisión ---
        emp2, _ = Empresa.objects.update_or_create(
            ruc="20509876543",
            defaults={
                "nombre": "Compañía Minera Cordillera S.A.",
                "tamano_max_mb": 40,
                "activo": True,
            },
        )
        area2, _ = Area.objects.update_or_create(
            empresa=emp2, nombre="Gestión Ambiental", defaults={"activo": True}
        )
        rev2 = self._crear_revision(
            emp2,
            anio=2026,
            nombre="TSF Principal · 2026",
            instalacion="TSF Principal",
            estado=EstadoInstalacionRevision.NUEVA,
            fase=FaseInstalacionRevision.CONSTRUCCION,
            capacidad="12 Mm³",
            vida_util="2050",
            fecha_inicio=date(2026, 2, 1),
            fecha_fin=date(2026, 11, 30),
            area=area2,
        )

        self._asignar_usuarios(emp1, emp2)

        # Evidencias + avance por preguntas (bandeja por revisar / parcial / aprobado)
        for rev, label in ((rev1a, emp1.nombre), (rev2, emp2.nombre)):
            stats = self._sembrar_bandeja_revision(rev)
            self.stdout.write(
                f"Bandeja {label} / {rev.nombre}: "
                f"aprobados={stats['aprobados']}, "
                f"por_revisar={stats['por_revisar']}, "
                f"parciales={stats['parciales']}, "
                f"preguntas_cumple={stats['preguntas_cumple']}."
            )

        n_act = self._sembrar_actividades_demo(emp1, emp2)
        self.stdout.write(f"Actividades demo: {n_act} (atrasadas + próximas).")
        n_acu = self._sembrar_acuerdos_demo(emp1, emp2)
        self.stdout.write(f"Acuerdos demo: {n_acu} (vencidos, próximos, cumplidos).")

        self._reportar(emp1, rev1a)
        self._reportar(emp1, rev1b)
        self._reportar(emp2, rev2)

        self.stdout.write(self.style.SUCCESS("Seed de empresas demo completado."))

    def _crear_revision(
        self,
        empresa: Empresa,
        *,
        anio: int,
        nombre: str,
        instalacion: str,
        estado: str,
        fase: str,
        capacidad: str,
        vida_util: str,
        fecha_inicio: date,
        fecha_fin: date,
        area: Area,
        activo: bool = True,
    ) -> Revision:
        revision, created = Revision.objects.update_or_create(
            empresa=empresa,
            anio=anio,
            nombre=nombre,
            defaults={
                "instalacion_deposito": instalacion,
                "estado": estado,
                "fase": fase,
                "capacidad": capacidad,
                "vida_util": vida_util,
                "fecha_inicio": fecha_inicio,
                "fecha_fin": fecha_fin,
                "activo": activo,
            },
        )
        if created:
            n_des = aplicar_deshabilitados_catalogo_a_revision(revision)
            self.stdout.write(
                f"  Revisión creada: {empresa.nombre} · {nombre} "
                f"(deshabilitados catálogo: {n_des})"
            )
        else:
            self.stdout.write(f"  Revisión ya existía: {empresa.nombre} · {nombre}")

        docs, vinc = aplicar_estandar_a_revision(revision, area)
        preguntas = EmpresaDocumentoPregunta.objects.filter(revision=revision).count()
        self.stdout.write(
            f"    Estándar: +{docs} docs, +{vinc} vínculos, "
            f"{preguntas} respuestas a preguntas sembradas."
        )
        return revision

    def _asignar_usuarios(self, emp1: Empresa, emp2: Empresa) -> None:
        # Gestores ven ambas
        for email in ("gestor@anddes.com", "smoke_gestor@test.local"):
            perfil = (
                PerfilUsuario.objects.filter(
                    user__email__iexact=email, tipo=TipoUsuario.GESTOR
                )
                .select_related("user")
                .first()
            )
            if perfil:
                perfil.empresas.set([emp1, emp2])
                self.stdout.write(f"  Gestor {email} → ambas empresas")

        # Clientes
        mapa = {
            "cliente@mina.com": emp1,
            "cliente@externa.com": emp2,
            "smoke_cliente@test.local": emp1,
        }
        for email, empresa in mapa.items():
            perfil = (
                PerfilUsuario.objects.filter(
                    user__email__iexact=email, tipo=TipoUsuario.CLIENTE
                )
                .select_related("user")
                .first()
            )
            if perfil:
                perfil.empresa = empresa
                perfil.save(update_fields=["empresa"])
                self.stdout.write(f"  Cliente {email} → {empresa.nombre}")

    def _usuario_cliente(self, empresa: Empresa):
        perfil = (
            PerfilUsuario.objects.filter(
                tipo=TipoUsuario.CLIENTE, empresa=empresa, activo=True
            )
            .select_related("user")
            .first()
        )
        return perfil.user if perfil else None

    def _asegurar_carga(
        self,
        doc: EmpresaDocumento,
        *,
        subido_por,
        estado: str,
        observacion: str,
    ) -> DocumentoCarga:
        DocumentoCarga.objects.filter(empresa_documento=doc, activo=True).update(
            activo=False
        )
        nombre = f"{doc.codigo}_evidencia_demo.pdf"
        carga = DocumentoCarga(
            empresa_documento=doc,
            nombre_original=nombre,
            observaciones=f"{_TAG_OBS} {observacion}",
            subido_por=subido_por,
            activo=True,
            estado_revision=estado,
        )
        if estado in (EstadoRevisionCarga.APROBADO, EstadoRevisionCarga.PARCIAL):
            carga.revisado_en = timezone.now()
        carga.archivo.save(nombre, ContentFile(_MINI_PDF), save=False)
        carga.save()
        return carga

    def _set_preguntas_vinculo(
        self, vinculo: EmpresaDocumentoCriterio, *, fraccion_cumple: float
    ) -> int:
        pregs = list(vinculo.preguntas.all().order_by("pregunta__orden", "pregunta__codigo"))
        if not pregs:
            return 0
        n_ok = int(len(pregs) * fraccion_cumple)
        if fraccion_cumple >= 1:
            n_ok = len(pregs)
        elif fraccion_cumple > 0 and n_ok == 0:
            n_ok = 1
        n = 0
        for i, resp in enumerate(pregs):
            nuevo = (
                EstadoRevisionPregunta.CUMPLE
                if i < n_ok
                else EstadoRevisionPregunta.PENDIENTE
            )
            if resp.estado_revision != nuevo:
                resp.estado_revision = nuevo
                resp.observacion = ""
                resp.save(
                    update_fields=["estado_revision", "observacion", "actualizado_en"]
                )
            if nuevo == EstadoRevisionPregunta.CUMPLE:
                n += 1
        sincronizar_estado_criterio_desde_preguntas(vinculo)
        return n

    def _sembrar_bandeja_revision(self, revision: Revision) -> dict[str, int]:
        """
        Crea evidencias demo para ejercitar la aprobación:
        - documentos 100% (preguntas CUMPLE + carga APROBADO)
        - por revisar sin avance (carga PENDIENTE, preguntas PENDIENTE)
        - por revisar con avance parcial de preguntas
        - parcial (algunos criterios al 100%, carga PARCIAL)
        """
        docs = list(
            EmpresaDocumento.objects.filter(revision=revision, activo=True)
            .annotate(n_crit=Count("vinculos_criterio"))
            .filter(n_crit__gt=0)
            .order_by("codigo")
        )
        stats = {
            "aprobados": 0,
            "por_revisar": 0,
            "parciales": 0,
            "preguntas_cumple": 0,
        }
        if not docs:
            return stats

        cliente = self._usuario_cliente(revision.empresa)
        n = len(docs)
        # Cupos (mínimo 1 en cada grupo si hay docs suficientes)
        n_aprob = max(1, int(n * 0.15))
        n_pend = max(2, int(n * 0.18))
        n_parcial_preg = max(2, int(n * 0.12))
        n_parcial_crit = max(1, int(n * 0.08))

        i = 0
        aprobados = docs[i : i + n_aprob]
        i += len(aprobados)
        pendientes = docs[i : i + n_pend]
        i += len(pendientes)
        parcial_preg = docs[i : i + n_parcial_preg]
        i += len(parcial_preg)
        parcial_crit = docs[i : i + n_parcial_crit]

        for doc in aprobados:
            doc.prioritario = True
            doc.revision_texto = "RevA"
            doc.revision_porcentaje = 100
            doc.save(
                update_fields=[
                    "prioritario",
                    "revision_texto",
                    "revision_porcentaje",
                    "actualizado_en",
                ]
            )
            for vinculo in EmpresaDocumentoCriterio.objects.filter(
                empresa_documento=doc
            ).prefetch_related("preguntas"):
                stats["preguntas_cumple"] += self._set_preguntas_vinculo(
                    vinculo, fraccion_cumple=1.0
                )
            self._asegurar_carga(
                doc,
                subido_por=cliente,
                estado=EstadoRevisionCarga.APROBADO,
                observacion="Evidencia ya aprobada (demo).",
            )
            sincronizar_estado_carga_documento(doc)
            stats["aprobados"] += 1

        for doc in pendientes:
            for vinculo in EmpresaDocumentoCriterio.objects.filter(
                empresa_documento=doc
            ).prefetch_related("preguntas"):
                self._set_preguntas_vinculo(vinculo, fraccion_cumple=0.0)
            self._asegurar_carga(
                doc,
                subido_por=cliente,
                estado=EstadoRevisionCarga.PENDIENTE,
                observacion="Por revisar: sin preguntas aprobadas aún.",
            )
            sincronizar_estado_carga_documento(doc)
            stats["por_revisar"] += 1

        for doc in parcial_preg:
            for vinculo in EmpresaDocumentoCriterio.objects.filter(
                empresa_documento=doc
            ).prefetch_related("preguntas"):
                stats["preguntas_cumple"] += self._set_preguntas_vinculo(
                    vinculo, fraccion_cumple=0.5
                )
            self._asegurar_carga(
                doc,
                subido_por=cliente,
                estado=EstadoRevisionCarga.PENDIENTE,
                observacion="Por revisar: avance parcial de preguntas (~50%).",
            )
            sincronizar_estado_carga_documento(doc)
            stats["por_revisar"] += 1

        for doc in parcial_crit:
            vinculos = list(
                EmpresaDocumentoCriterio.objects.filter(empresa_documento=doc)
                .prefetch_related("preguntas")
                .order_by("criterio__codigo")
            )
            mitad = max(1, len(vinculos) // 2)
            for idx, vinculo in enumerate(vinculos):
                frac = 1.0 if idx < mitad else 0.0
                stats["preguntas_cumple"] += self._set_preguntas_vinculo(
                    vinculo, fraccion_cumple=frac
                )
            self._asegurar_carga(
                doc,
                subido_por=cliente,
                estado=EstadoRevisionCarga.PARCIAL,
                observacion="Aprobación parcial: algunos criterios al 100%.",
            )
            sincronizar_estado_carga_documento(doc)
            stats["parciales"] += 1

        return stats

    def _usuario_gestor(self):
        for email in ("gestor@anddes.com", "smoke_gestor@test.local"):
            perfil = (
                PerfilUsuario.objects.filter(
                    user__email__iexact=email, tipo=TipoUsuario.GESTOR, activo=True
                )
                .select_related("user")
                .first()
            )
            if perfil:
                return perfil.user
        perfil = (
            PerfilUsuario.objects.filter(tipo=TipoUsuario.GESTOR, activo=True)
            .select_related("user")
            .first()
        )
        return perfil.user if perfil else None

    def _asegurar_responsable(self, area: Area, *, nombre: str, dni: str) -> Responsable:
        resp, _ = Responsable.objects.update_or_create(
            area=area,
            dni=dni,
            defaults={
                "nombre": nombre,
                "telefono": "999000111",
                "correo": f"demo.{dni}@example.com",
                "activo": True,
            },
        )
        return resp

    def _dt_local(self, d: date, hour: int = 10, minute: int = 0):
        naive = datetime.combine(d, time(hour, minute))
        if timezone.is_naive(naive):
            return timezone.make_aware(naive)
        return naive

    def _upsert_actividad(
        self,
        *,
        titulo: str,
        descripcion: str,
        empresa: Empresa | None,
        area: Area | None,
        responsable: Responsable | None,
        inicio,
        fin,
        estado: str,
        tipo: str,
        alcance: str,
        gestor,
        docs: list | None = None,
    ) -> Actividad:
        defaults = {
            "descripcion": descripcion,
            "tipo": tipo,
            "alcance": alcance,
            "empresa": empresa,
            "area": area,
            "responsable": responsable,
            "inicio": inicio,
            "fin": fin,
            "estado": estado,
            "creado_por": gestor,
            "asignado_a": gestor,
            "enviar_correo": alcance == AlcanceActividad.CON_CLIENTE,
            "enlace_reunion": (
                "https://teams.microsoft.com/l/meetup-join/demo"
                if tipo == TipoActividad.GENERAL
                else ""
            ),
        }
        act, _ = Actividad.objects.update_or_create(
            titulo=titulo,
            empresa=empresa,
            defaults=defaults,
        )
        if docs:
            act.documentos.set(docs[:3])
        return act

    def _sembrar_actividades_demo(self, emp1: Empresa, emp2: Empresa) -> int:
        """Actividades atrasadas (PASADA) y próximas (PROGRAMADA) para el calendario."""
        gestor = self._usuario_gestor()
        if gestor is None:
            self.stdout.write(
                self.style.WARNING(
                    "Sin usuario gestor: no se sembraron actividades demo."
                )
            )
            return 0

        hoy = timezone.localdate()
        area1 = Area.objects.filter(empresa=emp1, activo=True).order_by("nombre").first()
        area2 = Area.objects.filter(empresa=emp2, activo=True).order_by("nombre").first()
        resp1 = (
            self._asegurar_responsable(
                area1, nombre="Carla Ríos (demo)", dni="70112233"
            )
            if area1
            else None
        )
        resp2 = (
            self._asegurar_responsable(
                area2, nombre="Luis Vega (demo)", dni="70445566"
            )
            if area2
            else None
        )

        docs1 = list(
            EmpresaDocumento.objects.filter(
                revision__empresa=emp1, revision__activo=True, activo=True
            ).order_by("codigo")[:6]
        )
        docs2 = list(
            EmpresaDocumento.objects.filter(
                revision__empresa=emp2, revision__activo=True, activo=True
            ).order_by("codigo")[:6]
        )

        creadas = 0
        specs = [
            # --- Atrasadas (quedarán PASADA) ---
            {
                "titulo": "[demo] Revisión de evidencias · Andes Sur (atrasada)",
                "descripcion": "Seguimiento semanal que quedó pendiente.",
                "empresa": emp1,
                "area": area1,
                "responsable": resp1,
                "inicio": self._dt_local(hoy - timedelta(days=7), 9),
                "fin": self._dt_local(hoy - timedelta(days=7), 10),
                "estado": EstadoActividad.PROGRAMADA,
                "tipo": TipoActividad.GENERAL,
                "alcance": AlcanceActividad.CON_CLIENTE,
                "docs": docs1,
            },
            {
                "titulo": "[demo] Entrega de plan de acción (atrasada)",
                "descripcion": "Compromiso de envío de plan correctivo.",
                "empresa": emp1,
                "area": area1,
                "responsable": resp1,
                "inicio": self._dt_local(hoy - timedelta(days=3), 15),
                "fin": self._dt_local(hoy - timedelta(days=3), 16),
                "estado": EstadoActividad.PROGRAMADA,
                "tipo": TipoActividad.ACUERDO,
                "alcance": AlcanceActividad.CON_CLIENTE,
                "docs": docs1[2:] if len(docs1) > 2 else docs1,
            },
            {
                "titulo": "[demo] Recordatorio interno GISTM (atrasada)",
                "descripcion": "Preparar paquete de revisión antes de la visita.",
                "empresa": None,
                "area": None,
                "responsable": None,
                "inicio": self._dt_local(hoy - timedelta(days=2), 11),
                "fin": self._dt_local(hoy - timedelta(days=2), 11, 30),
                "estado": EstadoActividad.PROGRAMADA,
                "tipo": TipoActividad.RECORDATORIO,
                "alcance": AlcanceActividad.PERSONAL,
                "docs": None,
            },
            {
                "titulo": "[demo] Kick-off Cordillera (atrasada)",
                "descripcion": "Reunión de arranque que no se cerró.",
                "empresa": emp2,
                "area": area2,
                "responsable": resp2,
                "inicio": self._dt_local(hoy - timedelta(days=5), 10),
                "fin": self._dt_local(hoy - timedelta(days=5), 11, 30),
                "estado": EstadoActividad.PROGRAMADA,
                "tipo": TipoActividad.GENERAL,
                "alcance": AlcanceActividad.CON_CLIENTE,
                "docs": docs2,
            },
            # --- Próximas ---
            {
                "titulo": "[demo] Reunión semanal Andes Sur",
                "descripcion": "Revisar documentos por aprobar y observaciones.",
                "empresa": emp1,
                "area": area1,
                "responsable": resp1,
                "inicio": self._dt_local(hoy + timedelta(days=1), 10),
                "fin": self._dt_local(hoy + timedelta(days=1), 11),
                "estado": EstadoActividad.PROGRAMADA,
                "tipo": TipoActividad.GENERAL,
                "alcance": AlcanceActividad.CON_CLIENTE,
                "docs": docs1,
            },
            {
                "titulo": "[demo] Cierre de observaciones D013",
                "descripcion": "Validar respuestas a no-cumple del documento D013.",
                "empresa": emp1,
                "area": area1,
                "responsable": resp1,
                "inicio": self._dt_local(hoy + timedelta(days=3), 14),
                "fin": self._dt_local(hoy + timedelta(days=3), 15),
                "estado": EstadoActividad.PROGRAMADA,
                "tipo": TipoActividad.ACUERDO,
                "alcance": AlcanceActividad.CON_CLIENTE,
                "docs": docs1[:2],
            },
            {
                "titulo": "[demo] Visita TSF Cordillera",
                "descripcion": "Recorrido de instalación y revisión de evidencias en campo.",
                "empresa": emp2,
                "area": area2,
                "responsable": resp2,
                "inicio": self._dt_local(hoy + timedelta(days=5), 9),
                "fin": self._dt_local(hoy + timedelta(days=5), 12),
                "estado": EstadoActividad.PROGRAMADA,
                "tipo": TipoActividad.GENERAL,
                "alcance": AlcanceActividad.CON_CLIENTE,
                "docs": docs2,
            },
            {
                "titulo": "[demo] Preparar informe mensual",
                "descripcion": "Armar resumen de avance GISTM para dirección.",
                "empresa": None,
                "area": None,
                "responsable": None,
                "inicio": self._dt_local(hoy + timedelta(days=2), 16),
                "fin": self._dt_local(hoy + timedelta(days=2), 17),
                "estado": EstadoActividad.PROGRAMADA,
                "tipo": TipoActividad.RECORDATORIO,
                "alcance": AlcanceActividad.PERSONAL,
                "docs": None,
            },
            {
                "titulo": "[demo] Seguimiento post-revisión Cordillera",
                "descripcion": "Chequear criterios parciales y nuevas cargas.",
                "empresa": emp2,
                "area": area2,
                "responsable": resp2,
                "inicio": self._dt_local(hoy + timedelta(days=10), 11),
                "fin": self._dt_local(hoy + timedelta(days=10), 12),
                "estado": EstadoActividad.PROGRAMADA,
                "tipo": TipoActividad.GENERAL,
                "alcance": AlcanceActividad.CON_CLIENTE,
                "docs": docs2[1:4] if len(docs2) > 1 else docs2,
            },
        ]

        for spec in specs:
            self._upsert_actividad(gestor=gestor, **spec)
            creadas += 1

        n_pasadas = len(sincronizar_actividades_pasadas(creado_por_id=gestor.pk))
        self.stdout.write(f"  Actividades marcadas PASADA: {n_pasadas}.")
        return creadas

    def _actividad_demo(self, titulo: str, empresa: Empresa | None) -> Actividad | None:
        qs = Actividad.objects.filter(titulo=titulo)
        if empresa is None:
            qs = qs.filter(empresa__isnull=True)
        else:
            qs = qs.filter(empresa=empresa)
        return qs.first()

    def _upsert_acuerdo(
        self,
        *,
        titulo: str,
        descripcion: str,
        actividad: Actividad,
        empresa: Empresa | None,
        fecha_compromiso,
        estado: str,
        gestor,
        docs: list | None = None,
    ) -> Acuerdo:
        acuerdo, _ = Acuerdo.objects.update_or_create(
            titulo=titulo,
            actividad=actividad,
            defaults={
                "descripcion": descripcion,
                "empresa": empresa or actividad.empresa,
                "fecha_compromiso": fecha_compromiso,
                "estado": estado,
                "creado_por": gestor,
            },
        )
        if docs:
            acuerdo.documentos.set(docs[:3])
        else:
            acuerdo.documentos.clear()
        return acuerdo

    def _sembrar_acuerdos_demo(self, emp1: Empresa, emp2: Empresa) -> int:
        """Acuerdos abiertos (vencidos / hoy / próximos) y cumplidos para la bandeja."""
        gestor = self._usuario_gestor()
        if gestor is None:
            self.stdout.write(
                self.style.WARNING("Sin usuario gestor: no se sembraron acuerdos demo.")
            )
            return 0

        hoy = timezone.localdate()
        ahora = timezone.now()

        act_andes = self._actividad_demo(
            "[demo] Reunión semanal Andes Sur", emp1
        ) or self._actividad_demo(
            "[demo] Revisión de evidencias · Andes Sur (atrasada)", emp1
        )
        act_cord = self._actividad_demo(
            "[demo] Visita TSF Cordillera", emp2
        ) or self._actividad_demo("[demo] Kick-off Cordillera (atrasada)", emp2)
        act_personal = self._actividad_demo(
            "[demo] Preparar informe mensual", None
        )

        if not act_andes or not act_cord:
            self.stdout.write(
                self.style.WARNING(
                    "Faltan actividades demo para colgar acuerdos; se omite seed de acuerdos."
                )
            )
            return 0

        docs1 = list(
            EmpresaDocumento.objects.filter(
                revision__empresa=emp1, revision__activo=True, activo=True
            ).order_by("codigo")[:4]
        )
        docs2 = list(
            EmpresaDocumento.objects.filter(
                revision__empresa=emp2, revision__activo=True, activo=True
            ).order_by("codigo")[:4]
        )

        specs = [
            {
                "titulo": "[demo] Subir evidencia firmada D001 (vencido)",
                "descripcion": "Compromiso atrasado: cargar PDF firmado del documento D001.",
                "actividad": act_andes,
                "empresa": emp1,
                "fecha_compromiso": self._dt_local(hoy - timedelta(days=4), 18),
                "estado": EstadoAcuerdo.ABIERTO,
                "docs": docs1[:1],
            },
            {
                "titulo": "[demo] Responder observaciones de revisión (vencido)",
                "descripcion": "Cerrar puntos observados en la última revisión consultor.",
                "actividad": act_andes,
                "empresa": emp1,
                "fecha_compromiso": self._dt_local(hoy - timedelta(days=1), 12),
                "estado": EstadoAcuerdo.ABIERTO,
                "docs": docs1[1:3],
            },
            {
                "titulo": "[demo] Entregar avance de criterios hoy",
                "descripcion": "Enviar paquete parcial de criterios priorizados.",
                "actividad": act_andes,
                "empresa": emp1,
                "fecha_compromiso": self._dt_local(hoy, 17),
                "estado": EstadoAcuerdo.ABIERTO,
                "docs": docs1,
            },
            {
                "titulo": "[demo] Plan de acción TSF (próximo)",
                "descripcion": "Enviar plan de acción post visita de campo.",
                "actividad": act_cord,
                "empresa": emp2,
                "fecha_compromiso": self._dt_local(hoy + timedelta(days=2), 16),
                "estado": EstadoAcuerdo.ABIERTO,
                "docs": docs2[:2],
            },
            {
                "titulo": "[demo] Actualizar responsables de área (próximo)",
                "descripcion": "Confirmar contactos y correos del área Gestión Ambiental.",
                "actividad": act_cord,
                "empresa": emp2,
                "fecha_compromiso": self._dt_local(hoy + timedelta(days=5), 10),
                "estado": EstadoAcuerdo.ABIERTO,
                "docs": None,
            },
            {
                "titulo": "[demo] Informe mensual borrador (próximo)",
                "descripcion": "Borrador interno del informe de avance GISTM.",
                "actividad": act_personal or act_andes,
                "empresa": emp1,
                "fecha_compromiso": ahora + timedelta(days=6, hours=2),
                "estado": EstadoAcuerdo.ABIERTO,
                "docs": None,
            },
            {
                "titulo": "[demo] Kick-off documentado (cumplido)",
                "descripcion": "Acta y lista de asistencia ya cargadas.",
                "actividad": act_cord,
                "empresa": emp2,
                "fecha_compromiso": self._dt_local(hoy - timedelta(days=10), 11),
                "estado": EstadoAcuerdo.CUMPLIDO,
                "docs": docs2[:1],
            },
            {
                "titulo": "[demo] Compromiso anulado (cancelado)",
                "descripcion": "Quedó sin efecto tras cambio de alcance del proyecto.",
                "actividad": act_andes,
                "empresa": emp1,
                "fecha_compromiso": self._dt_local(hoy + timedelta(days=1), 9),
                "estado": EstadoAcuerdo.CANCELADO,
                "docs": None,
            },
        ]

        n = 0
        for spec in specs:
            self._upsert_acuerdo(gestor=gestor, **spec)
            n += 1
        n_venc = len(sincronizar_acuerdos_vencidos())
        self.stdout.write(f"  Acuerdos marcados VENCIDO: {n_venc}.")
        return n

    def _reportar(self, empresa: Empresa, revision: Revision) -> None:
        progreso = calcular_progreso_revision(revision)
        docs = EmpresaDocumento.objects.filter(revision=revision).count()
        vinc = EmpresaDocumentoCriterio.objects.filter(revision=revision).count()
        preg = EmpresaDocumentoPregunta.objects.filter(revision=revision).count()
        huerfanos = progreso.criterios_huerfanos_count
        self.stdout.write(
            self.style.SUCCESS(
                f"OK {empresa.nombre} | {revision.nombre} "
                f"(activo={revision.activo}): "
                f"docs={docs}, vínculos={vinc}, preguntas={preg}, "
                f"criterios {progreso.criterios_completos}/{progreso.criterios_total} "
                f"({progreso.criterios_pct}%), "
                f"docs avance {progreso.docs_cargados}/{progreso.docs_total} "
                f"({progreso.docs_pct}%), por revisar={progreso.docs_por_revisar}, "
                f"huérfanos={huerfanos}"
            )
        )
        # Coherencia: vínculos sembrados deben tener ≥1 pregunta
        sin_preg = (
            EmpresaDocumentoCriterio.objects.filter(revision=revision)
            .annotate(n=Count("preguntas"))
            .filter(n=0)
            .count()
        )
        if sin_preg:
            self.stdout.write(
                self.style.WARNING(
                    f"  ¡{sin_preg} vínculos sin preguntas sembradas!"
                )
            )
        else:
            self.stdout.write("  Todos los vínculos tienen preguntas sembradas.")
