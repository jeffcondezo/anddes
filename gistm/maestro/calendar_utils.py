from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from typing import Iterable

from django.utils import timezone


def mes_calendario(anio: int, mes: int, actividades: Iterable) -> list[list[dict]]:
    """
    Matriz de semanas → días para un mes.
    Cada celda: {date, in_month, activities[]}
    """
    cal = calendar.Calendar(firstweekday=0)  # lunes=0 en Europe... usamos Monday
    # Python calendar: Monday=0 if set
    cal = calendar.Calendar(firstweekday=calendar.MONDAY)

    by_day: dict[date, list] = {}
    for act in actividades:
        inicio = act.inicio
        if timezone.is_aware(inicio):
            inicio = timezone.localtime(inicio)
        d = inicio.date()
        by_day.setdefault(d, []).append(act)

    weeks: list[list[dict]] = []
    for week in cal.monthdatescalendar(anio, mes):
        row = []
        for day in week:
            row.append(
                {
                    "date": day,
                    "in_month": day.month == mes,
                    "is_today": day == timezone.localdate(),
                    "activities": by_day.get(day, []),
                }
            )
        weeks.append(row)
    return weeks


def parse_anio_mes(request_get) -> tuple[int, int]:
    today = timezone.localdate()
    try:
        anio = int(request_get.get("anio") or today.year)
        mes = int(request_get.get("mes") or today.month)
    except (TypeError, ValueError):
        return today.year, today.month
    if mes < 1 or mes > 12:
        mes = today.month
    if anio < 2000 or anio > 2100:
        anio = today.year
    return anio, mes


def mes_anterior(anio: int, mes: int) -> tuple[int, int]:
    d = date(anio, mes, 1) - timedelta(days=1)
    return d.year, d.month


def mes_siguiente(anio: int, mes: int) -> tuple[int, int]:
    if mes == 12:
        return anio + 1, 1
    return anio, mes + 1
