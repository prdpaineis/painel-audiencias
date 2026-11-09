#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Painel "Audiencias sem contestacao" - extracao via XML-RPC (Odoo MMP v10), SOMENTE LEITURA.

Janela: de hoje (BRT) ate o domingo da semana alvo. Seg-Qui: semana atual.
Sex-Dom: semana seguinte. (ODOO_INICIO / ODOO_FIM = AAAA-MM-DD sobrescrevem.)
Audiencia = dossie.dossie.data_audiencia_redesignada (se houver) senao data_audiencia_inicial.
Gera docs/index.html a partir de docs/painel_template.html. Uso: python extrair.py
"""
import os, sys, json, xmlrpc.client
from pathlib import Path
from datetime import datetime, timedelta, timezone

AQUI = Path(__file__).resolve().parent
BRT = timezone(timedelta(hours=-3))
CANDIDATOS = [Path(os.environ["ODOO_ENV_FILE"]) if os.environ.get("ODOO_ENV_FILE") else None,
              AQUI / "odoo-mmp.env"]
ENV = next((c for c in CANDIDATOS if c and c.exists()), None)

A_VALIDAR, A_ELABORAR, A_REFAZER = 860, 859, 865
A_VERIF_PARECER, A_SOLIC_PARECER, A_VALID_PARECER = 862, 1013, 1066
A_PROTOCOLAR, A_VERIF_PROTOC = 885, 1074
SEM_PECA = [A_REFAZER, A_ELABORAR, A_VALID_PARECER, A_SOLIC_PARECER, A_VERIF_PARECER]  # ordem de exibicao
TODAS = [A_VALIDAR, A_PROTOCOLAR, A_VERIF_PROTOC] + SEM_PECA
GRUPOS_EXCLUIDOS = {"gol"}   # fora do escopo do painel (igual a proposta original)
NOME = {860: "Validar contestação", 859: "Elaborar contestação", 865: "Refazer contestação",
        862: "Verificar parecer", 1013: "Solicitar parecer", 1066: "Validar parecer",
        885: "Protocolar", 1074: "Verificar protocolar para audiência"}


def conectar():
    if ENV is not None:
        for l in ENV.read_text(encoding="utf-8").splitlines():
            if "=" in l and not l.startswith("#"):
                k, _, v = l.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    if not all(k in os.environ for k in ("ODOO_URL", "ODOO_DB", "ODOO_LOGIN", "ODOO_PASSWORD")):
        sys.exit("ERRO: credenciais nao encontradas (odoo-mmp.env ou variaveis de ambiente).")
    url, db = os.environ["ODOO_URL"].rstrip("/"), os.environ["ODOO_DB"]
    lg, pw = os.environ["ODOO_LOGIN"], os.environ["ODOO_PASSWORD"]
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, lg, pw, {})
    if not uid:
        sys.exit("ERRO: login recusado pelo Odoo.")
    m = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object", allow_none=True)
    return lambda model, met, *a, **kw: m.execute_kw(db, uid, pw, model, met, list(a), kw)


def janela(hoje):
    if os.environ.get("ODOO_INICIO") and os.environ.get("ODOO_FIM"):
        return (datetime.fromisoformat(os.environ["ODOO_INICIO"]).date(),
                datetime.fromisoformat(os.environ["ODOO_FIM"]).date())
    d = hoje.date()
    seg = d - timedelta(days=d.weekday())
    if d.weekday() >= 4:
        seg += timedelta(days=7)
    return d, seg + timedelta(days=6)


def utc(dia, fim=False):
    dt = datetime(dia.year, dia.month, dia.day, tzinfo=BRT) + (timedelta(days=1) if fim else timedelta())
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def classificar(acoes):
    """acoes: {action_id: set(estados)} -> (status, action_id em aberto a exibir)"""
    g = lambda a: acoes.get(a, set())
    if "d" in g(A_VALIDAR):
        if "d" in g(A_PROTOCOLAR):
            return 4, None
        for a in (A_PROTOCOLAR, A_VERIF_PROTOC):
            if "i" in g(a):
                return 3, a
        return 3, None          # validada, sem protocolo concluido nem aberto
    if "i" in g(A_VALIDAR):
        return 2, A_VALIDAR
    for a in SEM_PECA:
        if "i" in g(a):
            return 1, a
    return 5, None              # sem acao de contestacao, ou Validar contestacao so cancelada


def main():
    x = conectar()
    agora = datetime.now(BRT)
    ini, fim = janela(agora)
    ini_u, fim_u = utc(ini), utc(fim, True)
    dossies = {}
    for c in ("data_audiencia_inicial", "data_audiencia_redesignada"):
        d = [(c, ">=", ini_u), (c, "<", fim_u)]
        n = x("dossie.dossie", "search_count", d)
        print(f"{c}: {n}")
        off = 0
        while off < n:
            for r in x("dossie.dossie", "search_read", d, fields=[
                    "name", "grupo_id", "situacao_id", "orgao_id",
                    "data_audiencia_inicial", "data_audiencia_redesignada"], limit=200, offset=off, order="id"):
                dossies[r["id"]] = r
            off += 200
    linhas = {}
    ids = list(dossies)
    for i in range(0, len(ids), 100):
        lote = ids[i:i + 100]
        dom = [("dossie_id", "in", lote), ("action_id", "in", TODAS)]
        n = x("project.task.action.line", "search_count", dom)
        off = 0
        while off < n:
            for l in x("project.task.action.line", "search_read", dom,
                       fields=["dossie_id", "action_id", "state", "user_id", "date_deadline"],
                       limit=500, offset=off, order="id"):
                linhas.setdefault(l["dossie_id"][0], []).append(l)
            off += 500

    dados = []
    for did, r in dossies.items():
        # data efetiva: redesignada vale, se houver; fora da janela -> ignora
        efet = r["data_audiencia_redesignada"] or r["data_audiencia_inicial"]
        if not (ini_u <= efet < fim_u):
            continue
        sit = r["situacao_id"][1] if r["situacao_id"] else ""
        if sit.lower().startswith("encerrad"):
            continue
        if r["grupo_id"] and r["grupo_id"][1].strip().lower() in GRUPOS_EXCLUIDOS:
            continue
        ls = linhas.get(did, [])
        acoes = {}
        for l in ls:
            acoes.setdefault(l["action_id"][0], set()).add(l["state"])
        s, a = classificar(acoes)
        usr, prazo, nome = "", "", ""
        if a:
            nome = NOME[a]
            ab = [l for l in ls if l["action_id"][0] == a and l["state"] == "i"]
            usr = next((l["user_id"][1] for l in ab if l["user_id"]), "sem responsável")
            prazo = min([l["date_deadline"] for l in ab if l["date_deadline"]] or [""])
        dt = (datetime.strptime(efet, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
              .astimezone(BRT).strftime("%Y-%m-%d %H:%M"))
        dados.append({"d": r["name"], "g": r["grupo_id"][1] if r["grupo_id"] else "Sem grupo",
                      "dt": dt, "s": s, "a": nome, "u": usr, "p": prazo, "r": s in (1, 2),
                      "sit": sit, "v": r["orgao_id"][1] if r["orgao_id"] else ""})
    dados.sort(key=lambda r: r["dt"])

    risco = [r for r in dados if r["s"] != 4]
    sem = [r for r in dados if r["s"] == 1]
    sem_resp = [r for r in sem if r["u"] == "sem responsável"]
    cont = {k: sum(1 for r in dados if r["s"] == k) for k in (1, 2, 3, 4, 5)}
    print(f"Janela {ini}..{fim}: {len(dados)} audiencias, risco {len(risco)}, status {cont}, "
          f"sem peca {len(sem)}, sem responsavel {len(sem_resp)}")
    br = lambda d: d.strftime("%d/%m/%Y")
    html = (AQUI / "painel_template.html").read_text(encoding="utf-8")
    for k, v in {"__PERIODO__": f"{br(ini)} a {br(fim)}", "__GERADO__": agora.strftime("%d/%m/%Y %H:%M"),
                 "__TOTAL__": str(len(dados)), "__RISCO__": str(len(risco)),
                 "__SEMPECA__": str(len(sem)), "__SEMRESP__": str(len(sem_resp)),
                 "__DATA__": json.dumps(dados, ensure_ascii=False)}.items():
        html = html.replace(k, v)
    (AQUI / "index.html").write_text(html, encoding="utf-8")
    print("index.html gerado")


if __name__ == "__main__":
    main()
