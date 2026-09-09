#!/usr/bin/env python3
"""
preencher_contrato.py — Gera um contrato VICTA preenchido a partir de um JSON
de dados. Substitui a versão anterior; a interface de linha de comando é a mesma.

Uso:
    python3 preencher_contrato.py dados.json saida.docx [--skill-dir DIR] [--forcar]

O que mudou em relação à versão anterior:
  1. Empacota/desempacota o .docx com a biblioteca padrão (zipfile). A versão
     antiga chamava /mnt/skills/public/docx/scripts/office/{unpack,pack}.py, que
     não existe mais em todos os ambientes — o script quebrava com CalledProcessError.
  2. O ESCOPO deixa de ser truncado. O template tem 3 marcadores (a, b, c); a
     versão antiga descartava em SILÊNCIO qualquer item a partir do 4º. Agora o
     parágrafo do 3º item é clonado para cada item extra, preservando estilo.
     Se o escopo tiver menos de 3 itens, os parágrafos sobrando são removidos
     (antes ficavam parágrafos vazios no Anexo 1).
  3. Confere de verdade o que a documentação prometia: soma dos percentuais =
     100% e soma das parcelas = valor total. Sem `--forcar`, para o processo.
  4. Quando o valor da parcela é calculado, a diferença de centavos é ajustada
     na última parcela, para a soma fechar exatamente com o total.
  5. Erros de dado faltando saem como mensagem legível, não como KeyError.

Formato do JSON: ver references/formato_dados.md.
"""

import json
import os
import re
import shutil
import sys
import zipfile

CAMPOS_OBRIGATORIOS = [
    "num_contrato", "data", "contratante", "contratada", "obra", "servico",
    "objeto_nome", "valor_num", "valor_extenso", "parcelas",
]


# --------------------------------------------------------------------------
# util
# --------------------------------------------------------------------------

def erro(msg):
    print(f"ERRO: {msg}", file=sys.stderr)
    sys.exit(1)


def brl_to_float(s):
    return float(str(s).replace("R$", "").replace(".", "").replace(",", ".").strip())


def float_to_brl(v):
    s = f"{v:,.2f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def esc(s):
    """Escapa XML e converte aspas/apóstrofos para smart quotes."""
    s = "" if s is None else str(s)
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    s = s.replace("\u0022", "&#x201D;").replace("\u0027", "&#x2019;")
    return s


def unpack(docx, destino):
    os.makedirs(destino, exist_ok=True)
    with zipfile.ZipFile(docx) as z:
        z.extractall(destino)


def pack(origem, saida):
    """Reempacota preservando [Content_Types].xml como primeira entrada."""
    saida = os.path.abspath(saida)
    os.makedirs(os.path.dirname(saida), exist_ok=True)
    if os.path.exists(saida):
        os.remove(saida)
    arquivos = []
    for raiz, _, nomes in os.walk(origem):
        for n in nomes:
            caminho = os.path.join(raiz, n)
            arquivos.append((caminho, os.path.relpath(caminho, origem)))
    arquivos.sort(key=lambda t: (t[1] != "[Content_Types].xml", t[1]))
    with zipfile.ZipFile(saida, "w", zipfile.ZIP_DEFLATED) as z:
        for caminho, rel in arquivos:
            z.write(caminho, rel.replace(os.sep, "/"))


# --------------------------------------------------------------------------
# tabela de parcelas
# --------------------------------------------------------------------------

def calcula_parcelas(parcelas, valor_total, forcar=False):
    """Devolve a lista de parcelas com valores resolvidos e confere as somas."""
    resolvidas, soma_pct, calculadas = [], 0.0, []

    for i, p in enumerate(parcelas):
        if "pct" not in p:
            erro(f"parcela {i + 1} sem o campo 'pct'.")
        pct_txt = str(p["pct"]).strip()
        pct = float(pct_txt.replace("%", "").replace(",", ".").strip())
        soma_pct += pct
        etapa = p.get("etapa", "Parcela Mensal")
        if p.get("valor"):
            valor = brl_to_float(p["valor"])
        else:
            valor = round(valor_total * pct / 100.0, 2)
            calculadas.append(i)
        resolvidas.append({"pct": pct_txt if pct_txt.endswith("%") else pct_txt + "%",
                           "etapa": etapa, "valor": valor})

    # ajusta centavos na última parcela calculada, para fechar com o total
    soma = round(sum(r["valor"] for r in resolvidas), 2)
    if calculadas and abs(soma - valor_total) < 0.05:
        resolvidas[calculadas[-1]]["valor"] = round(
            resolvidas[calculadas[-1]]["valor"] + (valor_total - soma), 2)
        soma = round(sum(r["valor"] for r in resolvidas), 2)

    problemas = []
    if abs(soma_pct - 100.0) > 0.01:
        problemas.append(f"os percentuais somam {soma_pct:.2f}%, não 100%")
    if abs(soma - valor_total) > 0.01:
        problemas.append(f"as parcelas somam R$ {float_to_brl(soma)}, "
                         f"diferente do valor total R$ {float_to_brl(valor_total)}")
    if problemas:
        msg = "; ".join(problemas)
        if forcar:
            print(f"AVISO (--forcar): {msg}.", file=sys.stderr)
        else:
            erro(f"{msg}. Corrija o JSON ou rode com --forcar se for intencional.")

    return resolvidas


def build_rows(parcelas, row_first, row_cont):
    linhas = []
    for i, p in enumerate(parcelas):
        tpl = row_first if i == 0 else row_cont
        linhas.append(tpl
                      .replace("{{NUM}}", str(i + 1))
                      .replace("{{PCT}}", esc(p["pct"]))
                      .replace("{{ETAPA}}", esc(p["etapa"]))
                      .replace("{{VALOR}}", esc("R$ " + float_to_brl(p["valor"]))))
    return "".join(linhas)


# --------------------------------------------------------------------------
# escopo (Anexo 1) — sem truncar
# --------------------------------------------------------------------------

def paragrafo_do_marcador(xml, marcador):
    """Retorna (inicio, fim, xml_do_paragrafo) do <w:p> que contém o marcador."""
    i = xml.find(marcador)
    if i == -1:
        return None
    ini = max(xml.rfind("<w:p>", 0, i), xml.rfind("<w:p ", 0, i))
    fim = xml.find("</w:p>", i)
    if ini == -1 or fim == -1:
        return None
    return ini, fim + len("</w:p>"), xml[ini:fim + len("</w:p>")]


def aplica_escopo(xml, itens):
    """Preenche {{ESCOPO_A/B/C}}. Itens além do 3º entram como parágrafos
    clonados do 3º (mesmo estilo). Marcadores sem item são removidos."""
    itens = [i for i in (itens or []) if str(i).strip()]

    for idx, chave in enumerate(["{{ESCOPO_A}}", "{{ESCOPO_B}}", "{{ESCOPO_C}}"]):
        loc = paragrafo_do_marcador(xml, chave)
        if loc is None:
            continue
        ini, fim, par = loc

        if idx < len(itens):
            novo = par.replace(chave, esc(str(itens[idx]).strip()))
            # o 3º parágrafo é o molde dos itens extras
            if idx == 2 and len(itens) > 3:
                extras = "".join(par.replace(chave, esc(str(t).strip()))
                                 for t in itens[3:])
                novo += extras
            xml = xml[:ini] + novo + xml[fim:]
        else:
            # nenhum item para este marcador: remove o parágrafo inteiro
            xml = xml[:ini] + xml[fim:]

    return xml


# --------------------------------------------------------------------------
# BIM x P2D
# --------------------------------------------------------------------------

def remove_anexo3(xml):
    anchor = xml.find("ANEXO 3 – DOCUMENTAÇÕES")
    if anchor != -1:
        cut_start = max(xml.rfind("<w:p>", 0, anchor), xml.rfind("<w:p ", 0, anchor))
        tbl_end = xml.find("</w:tbl>", anchor)
        cut_end = tbl_end + len("</w:tbl>") if tbl_end != -1 else anchor
        xml = xml[:cut_start] + xml[cut_end:]

    xml = xml.replace(
        "ANEXO 1 – OBJETO, ANEXO 2 –– DECLARAÇÃO E ANEXO 3 - DOCUMENTAÇÕES,",
        "ANEXO 1 – OBJETO, ANEXO 2 –– DECLARAÇÃO,")
    xml = re.sub(r"ANEXO 1 – OBJETO, ANEXO 2 ––? DECLARAÇÃO E ANEXO 3.*?,",
                 "ANEXO 1 – OBJETO, ANEXO 2 –– DECLARAÇÃO,", xml)

    # remove parágrafos vazios antes do <w:sectPr> final (evita página em branco)
    final_sect = xml.rfind("<w:sectPr")
    if final_sect != -1:
        head, tail = xml[:final_sect], xml[final_sect:]
        para_final = re.compile(r"<w:p\b(?:(?!<w:p\b).)*?</w:p>\s*$", re.DOTALL)
        while True:
            m = para_final.search(head)
            if not m:
                break
            txt = "".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", m.group(0), re.DOTALL))
            if txt.strip():
                break
            head = head[:m.start()] + head[m.end():]
        xml = head + tail
    return xml


# --------------------------------------------------------------------------

def main():
    args = [a for a in sys.argv[1:]]
    forcar = "--forcar" in args
    if forcar:
        args.remove("--forcar")
    skill_dir = None
    if "--skill-dir" in args:
        i = args.index("--skill-dir")
        skill_dir = args[i + 1]
        del args[i:i + 2]
    if len(args) < 2:
        erro("uso: preencher_contrato.py dados.json saida.docx "
             "[--skill-dir DIR] [--forcar]")
    dados_path, saida_path = args[0], args[1]

    if skill_dir is None:
        skill_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    assets = os.path.join(skill_dir, "assets")
    template = os.path.join(assets, "template_contrato.docx")
    for f in (template, os.path.join(assets, "row_first.xml"),
              os.path.join(assets, "row_cont.xml")):
        if not os.path.exists(f):
            erro(f"arquivo não encontrado: {f} (use --skill-dir)")

    row_first = open(os.path.join(assets, "row_first.xml"), encoding="utf-8").read()
    row_cont = open(os.path.join(assets, "row_cont.xml"), encoding="utf-8").read()

    try:
        with open(dados_path, encoding="utf-8") as f:
            d = json.load(f)
    except json.JSONDecodeError as e:
        erro(f"JSON inválido em {dados_path}: {e}")

    faltando = [c for c in CAMPOS_OBRIGATORIOS if not d.get(c)]
    for parte in ("contratante", "contratada"):
        if isinstance(d.get(parte), dict):
            for sub in ("nome", "qualificacao"):
                if not d[parte].get(sub):
                    faltando.append(f"{parte}.{sub}")
    if faltando:
        erro("faltam campos no JSON: " + ", ".join(faltando))

    marcadores_sobrando = [m for m in re.findall(r"\[[A-ZÇÃÕ ]+A INFORMAR\]",
                                                 json.dumps(d, ensure_ascii=False))]
    if marcadores_sobrando:
        print("AVISO: o JSON contém marcadores não preenchidos: "
              + ", ".join(sorted(set(marcadores_sobrando))), file=sys.stderr)

    work = "/tmp/contrato_build"
    shutil.rmtree(work, ignore_errors=True)
    unpacked = os.path.join(work, "unpacked")
    unpack(template, unpacked)

    docxml_path = os.path.join(unpacked, "word", "document.xml")
    xml = open(docxml_path, encoding="utf-8").read()

    # 1) parcelas
    valor_total = brl_to_float(d["valor_num"])
    parcelas = calcula_parcelas(d["parcelas"], valor_total, forcar)
    new_rows = build_rows(parcelas, row_first, row_cont)

    trs = list(re.finditer(r"<w:tr\b.*?</w:tr>", xml, re.DOTALL))
    model_rows = [m for m in trs if re.search(r"\{\{P\d+_", m.group(0))]
    if model_rows:
        xml = xml[:model_rows[0].start()] + new_rows + xml[model_rows[-1].end():]
    else:
        print("AVISO: linhas-modelo da tabela de parcelas não encontradas no template.",
              file=sys.stderr)

    # 2) escopo (antes das substituições simples, porque mexe em parágrafos)
    xml = aplica_escopo(xml, d.get("escopo", []))

    # 3) campos simples
    subs = {
        "{{NUM_CONTRATO}}": d["num_contrato"],
        "{{DATA}}": d["data"],
        "{{CONTRATANTE_NOME}}": d["contratante"]["nome"],
        "{{CONTRATANTE_QUALIFICACAO}}": d["contratante"]["qualificacao"],
        "{{CONTRATADA_NOME}}": d["contratada"]["nome"],
        "{{CONTRATADA_QUALIFICACAO}}": d["contratada"]["qualificacao"],
        "{{OBJETO_NOME}}": d["objeto_nome"],
        "{{OBJETO_COMPLEMENTO}}": d.get("objeto_complemento", ""),
        "{{VALOR_NUM}}": d["valor_num"],
        "{{VALOR_EXTENSO}}": d["valor_extenso"],
        "{{NUM_PARCELAS}}": str(len(parcelas)),
        "{{OBRA}}": d["obra"],
        "{{SERVICO}}": d["servico"],
        "{{ANEXO1_ETAPAS}}": d.get("anexo1_etapas", ""),
    }
    for k, v in subs.items():
        xml = xml.replace(k, esc(v))

    # 4) BIM x P2D
    if str(d.get("tipo", "BIM")).upper() != "BIM":
        xml = remove_anexo3(xml)

    open(docxml_path, "w", encoding="utf-8").write(xml)

    # 5) reempacota
    pack(unpacked, saida_path)

    # 6) confere marcadores remanescentes
    leftover = sorted(set(re.findall(r"\{\{[^}]+\}\}", xml)))
    if leftover:
        print(f"AVISO: marcadores não preenchidos no documento: {leftover}",
              file=sys.stderr)

    n_escopo = len([i for i in d.get("escopo", []) if str(i).strip()])
    print(f"Contrato gerado: {saida_path}")
    print(f"  tipo {d.get('tipo', 'BIM')} · {len(parcelas)} parcelas · "
          f"{n_escopo} itens de escopo · total R$ {float_to_brl(valor_total)}")


if __name__ == "__main__":
    main()
