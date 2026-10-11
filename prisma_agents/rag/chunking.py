"""Fragmentadores del corpus: normas por artículo, orientaciones por bloques con solape, currículo por OA."""
import re
from dataclasses import dataclass, field

from rag.nombres import asignatura_legible, curso_legible


@dataclass
class Fragmento:
    ruta: str
    texto: str
    metadata: dict = field(default_factory=dict)


# Cada página de los PDF de LeyChile repite este encabezado.
_ENCABEZADO_BCN = re.compile(r"(?im)^.*(biblioteca del congreso nacional|www\.leychile\.cl).*$")
_ARTICULO = re.compile(r"(?im)^[ \t]*(art[íi]culo\s+(\d+|[úu]nico)\s*[º°]?(?:\s+(bis|ter|qu[áa]ter))?)\s*[\.\-–—:]")


def limpiar_encabezados(texto: str) -> str:
    return _ENCABEZADO_BCN.sub("", texto)


def _partir(cuerpo: str, ruta: str, metadata: dict, max_chars: int) -> list[Fragmento]:
    if len(cuerpo) <= max_chars:
        return [Fragmento(ruta, cuerpo, dict(metadata))]
    bloques: list[str] = []
    for par in re.split(r"\n\s*\n", cuerpo):
        par = par.strip()
        while len(par) > max_chars:
            bloques.append(par[:max_chars])
            par = par[max_chars:]
        if par:
            bloques.append(par)
    partes: list[str] = []
    actual = ""
    for b in bloques:
        if actual and len(actual) + 2 + len(b) > max_chars:
            partes.append(actual)
            actual = b
        else:
            actual = f"{actual}\n\n{b}" if actual else b
    if actual:
        partes.append(actual)
    return [Fragmento(f"{ruta} (parte {i})", p, dict(metadata)) for i, p in enumerate(partes, start=1)]


def fragmentar_norma(texto: str, decreto: str, max_chars: int = 3200) -> list[Fragmento]:
    texto = limpiar_encabezados(texto.replace("\r\n", "\n")).strip()
    marcas = list(_ARTICULO.finditer(texto))
    if not marcas:
        return _partir(texto, "Texto completo", {"decreto": decreto}, max_chars) if texto else []
    fragmentos: list[Fragmento] = []
    previo = texto[: marcas[0].start()].strip()
    if len(previo) >= 200:
        fragmentos += _partir(previo, "Considerandos", {"decreto": decreto}, max_chars)
    for i, m in enumerate(marcas):
        fin = marcas[i + 1].start() if i + 1 < len(marcas) else len(texto)
        sufijo = (m.group(3) or "").lower()
        numero = m.group(2) + (f" {sufijo}" if sufijo else "")
        fragmentos += _partir(texto[m.start():fin].strip(), f"Art. {numero}", {"decreto": decreto, "articulo": numero}, max_chars)
    return fragmentos


def fragmentar_orientacion(paginas: list[str], max_chars: int = 2800, solape: int = 280) -> list[Fragmento]:
    unidades: list[tuple[int, str]] = []
    for n, pagina in enumerate(paginas, start=1):
        for par in re.split(r"\n\s*\n", pagina):
            par = re.sub(r"\s+", " ", par).strip()
            while len(par) > max_chars:
                unidades.append((n, par[:max_chars]))
                par = par[max_chars:]
            if par:
                unidades.append((n, par))

    fragmentos: list[Fragmento] = []
    buf, pags = "", []

    def cerrar():
        if buf.strip():
            ruta = f"p. {pags[0]}" if pags[0] == pags[-1] else f"pp. {pags[0]}–{pags[-1]}"
            fragmentos.append(Fragmento(ruta, buf.strip(), {"paginas": [pags[0], pags[-1]]}))

    for n, par in unidades:
        if buf and len(buf) + 1 + len(par) > max_chars:
            cerrar()
            buf, pags = buf[-solape:], [pags[-1]]
        buf = f"{buf} {par}".strip()
        pags.append(n)
    cerrar()
    return fragmentos


def fragmentar_curriculo(html: str, asignatura: str, curso: str, modalidad: str | None = None) -> list[Fragmento]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    nombre_asig, nombre_curso = asignatura_legible(asignatura), curso_legible(curso)
    eje, fragmentos = "", []
    for nodo in soup.select("h3.link, div.item-wrapper"):
        if nodo.name == "h3":
            eje = nodo.get_text(" ", strip=True)
            continue
        titulo = nodo.select_one("span.oa-title")
        cuerpo = nodo.select_one(".field--name-description")
        if not titulo or not cuerpo or not titulo.get_text().strip().lower().startswith("objetivo de aprendizaje"):
            continue
        codigo_largo = " ".join(titulo.get_text().split())[len("Objetivo de aprendizaje"):].strip()   # "MA03 OA 01"
        oa = " ".join(codigo_largo.split()[-2:])                                                         # "OA 01"
        texto = " ".join(cuerpo.get_text(" ", strip=True).split())
        meta = {"asignatura": asignatura, "curso": curso, "eje": eje, "oa": codigo_largo,
                "priorizado": "prioritized" in (nodo.get("class") or [])}
        if modalidad:
            meta["modalidad"] = modalidad
        fragmentos.append(Fragmento(
            f"{nombre_curso} · {nombre_asig} · {oa}",
            f"{nombre_asig} {nombre_curso} — {eje}. {codigo_largo}: {texto}",
            meta,
        ))
    return fragmentos
