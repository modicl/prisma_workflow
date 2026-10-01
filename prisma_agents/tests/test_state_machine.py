"""Validación estructural de la máquina de estados: destinos, alcanzabilidad, esperas con token, reintentos y que no viaje PII."""
import json
import os
import re

ASL = os.path.join(os.path.dirname(__file__), "..", "..", "statemachine", "prisma_flow.asl.json")
CLAVES_PERMITIDAS = {"phase", "session_id", "attempt", "feedback_agent", "task_token", "status"}


def cargar() -> dict:
    with open(ASL, encoding="utf-8") as f:
        return json.load(f)


def destinos(estado: dict) -> list:
    d = [estado[k] for k in ("Next", "Default") if k in estado]
    d += [c["Next"] for c in estado.get("Choices", [])]
    d += [c["Next"] for c in estado.get("Catch", [])]
    return d


def alcanzables(definicion: dict) -> set:
    estados, vistos, pila = definicion["States"], set(), [definicion["StartAt"]]
    while pila:
        n = pila.pop()
        if n in vistos:
            continue
        vistos.add(n)
        pila.extend(destinos(estados[n]))
    return vistos


def tareas(definicion: dict) -> dict:
    return {n: e for n, e in definicion["States"].items() if e["Type"] == "Task"}


def test_el_inicio_existe():
    d = cargar()
    assert d["StartAt"] in d["States"]


def test_todos_los_destinos_existen():
    d = cargar()
    for nombre, estado in d["States"].items():
        for destino in destinos(estado):
            assert destino in d["States"], f"{nombre} apunta a {destino}, que no existe"


def test_no_hay_estados_huerfanos():
    d = cargar()
    assert alcanzables(d) == set(d["States"]), f"huérfanos: {set(d['States']) - alcanzables(d)}"


def test_todo_estado_no_terminal_tiene_salida():
    for nombre, e in cargar()["States"].items():
        if e["Type"] in ("Succeed", "Fail"):
            continue
        assert destinos(e) or e.get("End"), f"{nombre} no tiene salida"
        if e["Type"] == "Choice":
            assert "Default" in e, f"{nombre} (Choice) no tiene Default"


def test_las_esperas_usan_waitForTaskToken_con_token_y_tiempos():
    d = cargar()
    for nombre in ("FaseA", "FaseB", "EsperarDocente"):
        e = d["States"][nombre]
        assert e["Resource"].endswith(":lambda:invoke.waitForTaskToken"), nombre
        assert e["Parameters"]["Payload"]["task_token.$"] == "$$.Task.Token", nombre
        assert "TimeoutSecondsPath" in e, nombre
    for nombre in ("FaseA", "FaseB"):
        assert "HeartbeatSecondsPath" in d["States"][nombre], nombre


def test_el_reintento_es_unico_y_solo_ante_heartbeat():
    """Cada reintento de una fase repite llamadas a Gemini: máximo 1 y únicamente si el worker dejó de dar señales."""
    d = cargar()
    for nombre in ("FaseA", "FaseB"):
        retry = d["States"][nombre]["Retry"]
        assert len(retry) == 1 and retry[0]["ErrorEquals"] == ["States.HeartbeatTimeout"] and retry[0]["MaxAttempts"] == 1


def test_el_docente_que_no_responde_termina_como_expirada():
    e = cargar()["States"]["EsperarDocente"]
    timeout = [c for c in e["Catch"] if c["ErrorEquals"] == ["States.Timeout"]]
    assert timeout and timeout[0]["Next"] == "PrepExpirada"
    assert cargar()["States"]["PrepExpirada"]["Result"] == {"status": "expired"}


def test_el_payload_solo_lleva_claves_permitidas():
    """Foco de revisión 5: por Step Functions no viaja texto de documentos ni comentarios del docente."""
    for nombre, e in tareas(cargar()).items():
        claves = {k.removesuffix(".$") for k in e["Parameters"]["Payload"]}
        assert claves <= CLAVES_PERMITIDAS, f"{nombre} envía {claves - CLAVES_PERMITIDAS}"


def test_el_unico_marcador_de_plantilla_es_el_arn_del_invoker():
    texto = open(ASL, encoding="utf-8").read()
    assert set(re.findall(r"\$\{(\w+)\}", texto)) == {"InvokerArn"}


def test_el_limite_de_intentos_del_docente_es_3():
    elecciones = cargar()["States"]["DecidirHitl"]["Choices"]
    rechazo_final = [c for c in elecciones if "And" in c]
    assert rechazo_final and rechazo_final[0]["Next"] == "PrepRechazada"
    assert {"Variable": "$.ctx.attempt", "NumericGreaterThanEquals": 3} in rechazo_final[0]["And"]


def test_el_siguiente_intento_suma_uno_y_lleva_el_agente_elegido():
    p = cargar()["States"]["ProximoIntento"]["Parameters"]
    assert p["attempt.$"] == "States.MathAdd($.ctx.attempt, 1)" and p["feedback_agent.$"] == "$.decision.agent_to_retry"


def test_los_resultados_de_negocio_terminan_en_succeed_y_los_errores_en_fail():
    e = cargar()["States"]
    assert e["Resultado"]["Default"] == "Fin" and e["Fin"]["Type"] == "Succeed"
    fallan = {c["Variable"] + "=" + c["StringEquals"] for c in e["Resultado"]["Choices"][0]["Or"]}
    assert fallan == {"$.final.status=error", "$.final.status=timeout"}
    assert e[e["Resultado"]["Choices"][0]["Next"]]["Type"] == "Fail"


def test_todo_camino_pasa_por_finalizar_antes_de_terminar():
    d = cargar()["States"]
    for nombre in ("PrepError", "PrepExpirada", "PrepRechazada", "FinalDesdeFase"):
        assert d[nombre]["Next"] == "Finalizar", nombre
