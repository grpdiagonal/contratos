import streamlit as st
import json
import subprocess
import os
import sys
import tempfile
import re
from datetime import date, datetime

try:
    from notion_client import Client
    from notion_client.errors import APIResponseError
except ImportError:
    st.error("Biblioteca `notion-client` não instalada.")
    st.stop()

# ============================================================================
# CONFIG
# ============================================================================

NOTION_TOKEN          = st.secrets.get("NOTION_TOKEN")          or os.getenv("NOTION_TOKEN")
DATABASE_ID           = st.secrets.get("DATABASE_ID")           or os.getenv("DATABASE_ID")
DATABASE_ID_COLIGADAS = st.secrets.get("DATABASE_ID_COLIGADAS") or os.getenv("DATABASE_ID_COLIGADAS") or "3dc3a59439718073ae51f0e831f0b425"

if not NOTION_TOKEN or not DATABASE_ID:
    st.error("❌ NOTION_TOKEN ou DATABASE_ID não configurados em Settings → Secrets.")
    st.stop()

notion = Client(auth=NOTION_TOKEN)

# ============================================================================
# ESTILOS
# ============================================================================

st.set_page_config(
    page_title="VICTA — Montador de Contratos",
    page_icon="📋",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
    section.main > div { padding-top: 1.5rem; }
    h1  { color: #1f5c4d; margin-bottom: 0.25rem; }
    h3  { margin-top: 0.5rem; margin-bottom: 0.5rem; color: #2d4a42; }
    .metadata { font-size: 0.8rem; color: #888; margin-bottom: 1rem; }
    div[data-testid="stHorizontalBlock"] { align-items: flex-start; }
</style>
""", unsafe_allow_html=True)

# ============================================================================
# FUNÇÕES DE LEITURA DO NOTION
# ============================================================================

def _get_prop(props, name, prop_type="rich_text"):
    if name not in props:
        return None
    prop = props[name]
    try:
        if prop_type == "title":
            return "".join(t["plain_text"] for t in prop.get("title", []))
        elif prop_type == "rich_text":
            return "".join(t["plain_text"] for t in prop.get("rich_text", []))
        elif prop_type == "select":
            sel = prop.get("select")
            return sel.get("name") if sel else None
        elif prop_type == "status":
            sel = prop.get("status")
            return sel.get("name") if sel else None
        elif prop_type == "multi_select":
            return [item["name"] for item in prop.get("multi_select", [])]
        elif prop_type == "date":
            val = prop.get("date") or {}
            return val.get("start")
        elif prop_type == "checkbox":
            return prop.get("checkbox", False)
        elif prop_type == "number":
            return prop.get("number")
    except Exception:
        return None
    return None


def _page_to_fornecedor(page):
    props = page["properties"]
    G = lambda name, t="rich_text": _get_prop(props, name, t)
    return {
        "id":                  page["id"],
        "nome":                G("Nome Empresa", "title") or G("PROJETISTA"),
        "responsavel_tecnico": G("Nome do Responsável Técnico"),
        "projeta_bim":         G("Projeta em BIM?", "checkbox"),
        "data_cadastro":       G("Data de Cadastro", "date"),
        "status_qualificacao": G("Status da Qualificação", "status"),
        "data_qualificacao":   G("Data de Qualificação", "date"),
        "validade_qualificacao": G("Validade da Qualificação", "date"),
        "status_geral":        G("Status Geral", "status"),
        "crea_cau":            G("CREA/CAU"),
        "cnpj":                G("CNPJ"),
        "email":               G("E-mail"),
        "telefone":            G("Telefone"),
        "representante_legal": (G("REPRESENTANTE LEGAL") or G("Representante Legal")
                                or G("Nome do Responsável Técnico")),
        "cpf":                 G("CPF"),
        "rg":                  G("RG"),
        "endereco":            G("ENDEREÇO") or G("Endereço") or G("Endereco"),
        "disciplinas":         G("DISCIPLINA", "multi_select") or G("Disciplina", "multi_select"),
        "referencias":         G("Referências"),
    }


def _page_to_coligada(page):
    props = page["properties"]
    G = lambda name, t="rich_text": _get_prop(props, name, t)
    return {
        "id":        page["id"],
        "codigo":    G("Código", "number"),
        "nome":      G("Nome Coligada", "title"),
        "nome_obra": G("Nome da Obra"),
        "cnpj":      G("CNPJ"),
        "endereco":  G("Endereço") or G("ENDEREÇO"),
    }


def _paginar_notion(database_id, label="registros"):
    MAX_PAGES = 50
    results, cursor, pagina = [], None, 0
    progresso = st.empty()
    try:
        while pagina < MAX_PAGES:
            pagina += 1
            progresso.caption(f"⏳ Carregando {label} — página {pagina}…")
            params = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            try:
                resp = notion.databases.query(database_id, **params)
            except APIResponseError as e:
                progresso.empty()
                st.error(f"Erro na API do Notion ({label}): {e}")
                return []
            results.extend(resp.get("results", []))
            has_more = resp.get("has_more", False)
            cursor   = resp.get("next_cursor")
            if not has_more or not cursor:
                break
        progresso.empty()
    except Exception as e:
        progresso.empty()
        st.error(f"Erro inesperado ({label}): {e}")
        return []
    return results


@st.cache_data(ttl=3600)
def puxar_fornecedores():
    pages = _paginar_notion(DATABASE_ID, "fornecedores")
    result = []
    for page in pages:
        try:
            f = _page_to_fornecedor(page)
            if f["nome"]:
                result.append(f)
        except Exception:
            pass
    return result


@st.cache_data(ttl=3600)
def puxar_coligadas():
    pages = _paginar_notion(DATABASE_ID_COLIGADAS, "coligadas")
    result = []
    for page in pages:
        try:
            c = _page_to_coligada(page)
            if c["nome"]:
                result.append(c)
        except Exception:
            pass
    return sorted(result, key=lambda x: x.get("codigo") or 999)


# ============================================================================
# FUNÇÕES AUXILIARES
# ============================================================================

def _validade_ok(data_str):
    if not data_str:
        return False
    try:
        return datetime.strptime(data_str[:10], "%Y-%m-%d").date() >= date.today()
    except ValueError:
        return False


def formata_qualificacao_spe(col):
    endereco = col.get("endereco") or "[ENDEREÇO A INFORMAR]"
    cnpj     = col.get("cnpj")    or "[CNPJ A INFORMAR]"
    return (
        f" com sede em {endereco}, inscrita no CNPJ sob o número {cnpj}, "
        f"neste ato representado pelo seu gerente/delegado abaixo assinado;"
    )


def formata_qualificacao_fornecedor(forn):
    endereco = forn.get("endereco")           or "[ENDEREÇO A INFORMAR]"
    cnpj     = forn.get("cnpj")               or "[CNPJ A INFORMAR]"
    rep      = forn.get("representante_legal") or "[REPRESENTANTE A INFORMAR]"
    rg       = forn.get("rg")                 or "[RG A INFORMAR]"
    cpf      = forn.get("cpf")                or "[CPF A INFORMAR]"
    return (
        f", com sede em {endereco}, inscrita no CNPJ sob o número {cnpj}, "
        f"neste ato representada legalmente e na forma estatutária pelo(a) "
        f"senhor(a) {rep}, portador(a) do RG {rg} e CPF {cpf};"
    )


# ── Valor por extenso ────────────────────────────────────────────────────────

_UNIDADES = ["", "um", "dois", "três", "quatro", "cinco", "seis", "sete",
             "oito", "nove", "dez", "onze", "doze", "treze", "quatorze",
             "quinze", "dezesseis", "dezessete", "dezoito", "dezenove"]
_DEZENAS  = ["", "", "vinte", "trinta", "quarenta", "cinquenta",
             "sessenta", "setenta", "oitenta", "noventa"]
_CENTENAS = ["", "cem", "duzentos", "trezentos", "quatrocentos", "quinhentos",
             "seiscentos", "setecentos", "oitocentos", "novecentos"]


def _centenas_ext(n):
    if n == 100:
        return "cem"
    c, resto = divmod(n, 100)
    d, u = divmod(resto, 10)
    partes = []
    if c:
        partes.append(_CENTENAS[c])
    if resto < 20:
        if resto:
            partes.append(_UNIDADES[resto])
    else:
        partes.append(_DEZENAS[d])
        if u:
            partes.append(_UNIDADES[u])
    return " e ".join(partes)


def numero_extenso(valor_str):
    try:
        limpo = str(valor_str).replace("R$", "").replace(" ", "")
        if "," in limpo:
            partes = limpo.replace(".", "").split(",")
            int_str, dec_str = partes[0], (partes[1] + "00")[:2]
        else:
            int_str, dec_str = limpo.replace(".", ""), "00"
        inteiro, centavos = int(int_str), int(dec_str)
        if inteiro == 0 and centavos == 0:
            return "zero"
        partes_ext = []
        if inteiro > 0:
            bilhoes, resto = divmod(inteiro, 1_000_000_000)
            milhoes, resto = divmod(resto, 1_000_000)
            mil, unid      = divmod(resto, 1_000)
            if bilhoes:
                partes_ext.append(f"{_centenas_ext(bilhoes)} {'bilhão' if bilhoes==1 else 'bilhões'}")
            if milhoes:
                partes_ext.append(f"{_centenas_ext(milhoes)} {'milhão' if milhoes==1 else 'milhões'}")
            if mil:
                partes_ext.append(f"{_centenas_ext(mil)} mil")
            if unid:
                partes_ext.append(_centenas_ext(unid))
            reais_ext = " e ".join(partes_ext) + (" real" if inteiro == 1 else " reais")
        else:
            reais_ext = ""
        if centavos > 0:
            cent_ext = _centenas_ext(centavos) + (" centavo" if centavos == 1 else " centavos")
        else:
            cent_ext = ""
        if reais_ext and cent_ext:
            return f"{reais_ext} e {cent_ext}"
        return reais_ext or cent_ext
    except Exception:
        return "[VALOR POR EXTENSO — CONFERIR]"


def brl(valor):
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


# ── Geração do docx ──────────────────────────────────────────────────────────

def _localizar_script():
    base = os.path.dirname(os.path.abspath(__file__))
    for c in [
        os.path.join(base, "preencher_contrato.py"),
        os.path.join(base, "scripts", "preencher_contrato.py"),
        "preencher_contrato.py",
    ]:
        if os.path.exists(c):
            return c
    return None


def _localizar_assets():
    base = os.path.dirname(os.path.abspath(__file__))
    for c in [os.path.join(base, "assets"), base, os.path.join(base, "scripts")]:
        if os.path.isfile(os.path.join(c, "template_contrato.docx")):
            return c
    return None


def gerar_docx(dados):
    script = _localizar_script()
    assets = _localizar_assets()
    if not script:
        return None, "preencher_contrato.py não encontrado."
    if not assets:
        return None, "Pasta de assets não encontrada."
    json_path = docx_path = ""
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as fj:
            json.dump(dados, fj, ensure_ascii=False)
            json_path = fj.name
        docx_path = tempfile.mktemp(suffix=".docx")
        result = subprocess.run(
            [sys.executable, script, json_path, docx_path,
             "--skill-dir", os.path.dirname(assets)],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0 and os.path.exists(docx_path):
            with open(docx_path, "rb") as fd:
                return fd.read(), ""
        return None, result.stderr or "Erro desconhecido."
    except subprocess.TimeoutExpired:
        return None, "Timeout (>60 s)."
    except Exception as e:
        return None, str(e)
    finally:
        for p in (json_path, docx_path):
            try:
                if p and os.path.exists(p):
                    os.unlink(p)
            except Exception:
                pass


# ============================================================================
# CARREGAR DADOS
# ============================================================================

st.title("VICTA · Montador de Contratos")

with st.spinner("Conectando ao Notion…"):
    fornecedores = puxar_fornecedores()
    coligadas    = puxar_coligadas()

if not fornecedores:
    st.error("Nenhum fornecedor encontrado — verifique o token e o Database ID.")
    st.stop()

# Disponíveis = todos exceto "Não qualificado"
disponiveis = [
    f for f in fornecedores
    if (f.get("status_qualificacao") or "").strip().lower() != "não qualificado"
]
bloqueados = [
    f for f in fornecedores
    if (f.get("status_qualificacao") or "").strip().lower() == "não qualificado"
]

st.markdown(
    f'<div class="metadata">🔄 Sincronizado do Notion · '
    f'{len(coligadas)} coligadas · {len(disponiveis)} fornecedores disponíveis · '
    f'{len(bloqueados)} bloqueados</div>',
    unsafe_allow_html=True,
)

tab_contrato, tab_inspecionar = st.tabs(["📝 Novo Contrato", "🔍 Inspecionar Dados"])

# ============================================================================
# ABA 1 — NOVO CONTRATO
# ============================================================================

with tab_contrato:

    # ── 1. PARTES ──────────────────────────────────────────────────────────────
    st.subheader("1. Partes do Contrato")

    col1, col2, col3 = st.columns([5, 1, 5])

    with col1:
        def label_coligada(c):
            cod  = c.get("codigo") or "—"
            obra = c.get("nome_obra") or c.get("nome") or "—"
            nome = c.get("nome") or ""
            sufixo = f" ({nome})" if nome and nome != obra else ""
            return f"{cod} · {obra}{sufixo}"

        opcoes_col  = {label_coligada(c): c for c in coligadas}
        labels_col  = sorted(opcoes_col.keys())
        nome_col    = st.selectbox(
            "🏢 SPE / Contratante",
            options=labels_col,
            index=None,
            placeholder="Digite para buscar…",
        )
        col_selecionada = opcoes_col.get(nome_col) if nome_col else None

        if col_selecionada:
            st.caption(
                f"**CNPJ:** {col_selecionada.get('cnpj') or '—'} · "
                f"**Obra:** {col_selecionada.get('nome_obra') or '—'}"
            )
            if not col_selecionada.get("endereco"):
                st.warning("⚠️ Endereço não preenchido nesta coligada.")

    with col2:
        tipo = st.radio("📄 Tipo", ["BIM", "P2D", "DISTRATO"])

    with col3:
        if tipo != "DISTRATO":
            disp_ord  = sorted(disponiveis, key=lambda f: f["nome"] or "")
            nome_proj = st.selectbox(
                "🏗️ Projetista / Contratada",
                options=[f["nome"] for f in disp_ord],
                index=None,
                placeholder="Digite para buscar…",
            )
            proj = next((f for f in disponiveis if f["nome"] == nome_proj), None)

            if proj:
                status = proj.get("status_qualificacao") or "—"
                val    = proj.get("validade_qualificacao") or "—"
                st.caption(
                    f"**CNPJ:** {proj.get('cnpj') or '—'} · "
                    f"**Status:** {status} · **Validade:** {val}"
                )
                faltando = [c for c in ("cnpj", "endereco", "representante_legal", "cpf", "rg")
                            if not proj.get(c)]
                if faltando:
                    st.warning(f"⚠️ Dados incompletos: {', '.join(faltando)}")
        else:
            proj = None
            st.info("Distrato não requer projetista.")

    st.divider()

    # ── 2. DADOS DO CONTRATO ───────────────────────────────────────────────────
    st.subheader("2. Dados do Contrato")

    col1, col2, col3, col4 = st.columns([2, 2, 2, 4])
    with col1:
        num_contrato = st.text_input("Nº do contrato", placeholder="2026.09.09")
    with col2:
        data_contrato = st.date_input("Data", value=date.today())
    with col3:
        valor_total_str = st.text_input("Valor total (R$)", placeholder="132.000,00")
        try:
            valor_total = float(
                valor_total_str.replace("R$", "").replace(".", "").replace(",", ".").strip()
            )
        except Exception:
            valor_total = 0.0
        if valor_total > 0:
            st.caption(numero_extenso(valor_total_str))
    with col4:
        objeto = st.text_input("Objeto/Serviço", placeholder="PROJETO EXECUTIVO DE ARQUITETURA – BIM")

    col1, col2 = st.columns([2, 4])
    with col1:
        obra = st.text_input(
            "Obra",
            value=col_selecionada.get("nome_obra") if col_selecionada else "",
            placeholder="Nome do empreendimento",
        )
    with col2:
        local = st.text_area("Local da obra", placeholder="Endereço completo da obra", height=70)

    st.divider()

    # ── 3. PARCELAS ────────────────────────────────────────────────────────────
    st.subheader("3. Parcelas")

    col_np, _ = st.columns([2, 8])
    with col_np:
        num_parcelas = st.number_input("Quantas parcelas?", min_value=1, max_value=10, value=3)

    # Cabeçalho
    hc = st.columns([2, 5, 3])
    hc[0].markdown("**Percentual (%)**")
    hc[1].markdown("**Etapa**")
    hc[2].markdown("**Valor calculado**")

    pcts, etapas = [], []
    for i in range(int(num_parcelas)):
        c1, c2, c3 = st.columns([2, 5, 3])
        with c1:
            pct = st.number_input(
                f"pct_{i}", min_value=0.0, max_value=100.0,
                value=round(100.0 / num_parcelas, 2),
                key=f"pct_{i}", label_visibility="collapsed",
            )
        with c2:
            etapa = st.text_input(
                f"etapa_{i}", value=f"Parcela {i+1}",
                key=f"etapa_{i}", label_visibility="collapsed",
            )
        with c3:
            if valor_total > 0:
                vc = round(valor_total * pct / 100.0, 2)
                st.text_input(f"val_{i}", value=brl(vc), disabled=True,
                              key=f"val_d_{i}", label_visibility="collapsed")
            else:
                st.text_input(f"val_{i}", value="", placeholder="—", disabled=True,
                              key=f"val_d_{i}", label_visibility="collapsed")
        pcts.append(pct)
        etapas.append(etapa)

    soma_pct = sum(pcts)
    if abs(soma_pct - 100.0) > 0.1:
        st.warning(f"⚠️ Percentuais somam {soma_pct:.2f}%, não 100 %")

    # Monta lista de parcelas para o script
    parcelas = []
    if valor_total > 0:
        acc = []
        for i, (pct, etapa) in enumerate(zip(pcts, etapas)):
            vc = round(valor_total * pct / 100.0, 2)
            if i == len(pcts) - 1:
                vc = round(valor_total - sum(acc), 2)
            acc.append(vc)
            parcelas.append({"pct": f"{pct:.2f}%".replace(".", ","), "etapa": etapa,
                             "valor": f"{vc:.2f}".replace(".", ",")})
    else:
        for pct, etapa in zip(pcts, etapas):
            parcelas.append({"pct": f"{pct:.2f}%".replace(".", ","), "etapa": etapa, "valor": ""})

    st.divider()

    # ── 4. ESCOPO ──────────────────────────────────────────────────────────────
    st.subheader("4. Escopo (Anexo 1)")

    col_ne, _ = st.columns([2, 8])
    with col_ne:
        num_itens = st.number_input("Quantos itens?", min_value=1, max_value=10, value=3)

    escopo = []
    for i in range(int(num_itens)):
        item = st.text_input(f"Item {i+1}", key=f"escopo_{i}")
        if item:
            escopo.append(item)

    if len(escopo) > 3:
        st.info(f"✓ {len(escopo)} itens — o parágrafo do 3º item será clonado para os extras.")

    st.divider()

    # ── GERAR ──────────────────────────────────────────────────────────────────
    if st.button("⚡ Gerar Contrato", type="primary", use_container_width=True):
        erros = []
        if not col_selecionada:
            erros.append("Escolha uma SPE/Coligada")
        if tipo != "DISTRATO" and not proj:
            erros.append("Escolha um projetista")
        if not objeto:
            erros.append("Informe o objeto/serviço")
        if valor_total <= 0:
            erros.append("Informe o valor total")
        if not escopo:
            erros.append("Informe pelo menos um item de escopo")
        if abs(soma_pct - 100.0) > 0.1:
            erros.append("Percentuais não somam 100 %")

        if erros:
            st.error("Não é possível gerar:\n" + "\n".join(f"• {e}" for e in erros))
        else:
            dados = {
                "tipo":        tipo,
                "num_contrato": num_contrato,
                "data":        data_contrato.strftime("%d/%m/%Y"),
                "contratante": {
                    "nome":         col_selecionada["nome"],
                    "qualificacao": formata_qualificacao_spe(col_selecionada),
                },
                "contratada": {
                    "nome":         proj["nome"] if proj else "—",
                    "qualificacao": formata_qualificacao_fornecedor(proj) if proj else "",
                },
                "obra":               obra or (col_selecionada.get("nome_obra") or ""),
                "servico":            objeto,
                "objeto_nome":        objeto,
                "objeto_complemento": f", para a obra {obra}, situada na {local}." if local else ".",
                "valor_num":     valor_total_str,
                "valor_extenso": numero_extenso(valor_total_str),
                "escopo":        escopo,
                "parcelas":      parcelas,
            }

            with st.spinner("Gerando contrato…"):
                docx_bytes, erro_msg = gerar_docx(dados)

            if docx_bytes:
                st.success("✓ Contrato gerado com sucesso!")
                nome_arq = f"Contrato_{tipo}_{num_contrato.replace('.', '-')}.docx"
                col_a, col_b = st.columns(2)
                with col_a:
                    st.download_button(
                        "📥 Baixar contrato (.docx)", data=docx_bytes,
                        file_name=nome_arq,
                        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        use_container_width=True,
                    )
                with col_b:
                    st.download_button(
                        "📋 Baixar dados (.json)",
                        data=json.dumps(dados, ensure_ascii=False, indent=2),
                        file_name=f"dados_{num_contrato.replace('.', '-')}.json",
                        mime="application/json",
                        use_container_width=True,
                    )
            else:
                st.error(f"Erro ao gerar contrato:\n{erro_msg}")

# ============================================================================
# ABA 2 — INSPECIONAR DADOS
# ============================================================================

with tab_inspecionar:

    st.subheader(f"Coligadas / SPEs ({len(coligadas)})")
    for c in coligadas:
        cod  = c.get("codigo") or "—"
        obra = c.get("nome_obra") or "—"
        with st.expander(f"🏢 {cod} · {c['nome']} — {obra}"):
            col1, col2 = st.columns(2)
            col1.write(f"**CNPJ:** {c.get('cnpj') or '—'}")
            col2.write(f"**Obra:** {obra}")
            st.text_area("Endereço:", value=c.get("endereco") or "", disabled=True,
                         height=55, key=f"ec_{c['id']}")

    st.divider()
    st.subheader(f"Fornecedores Disponíveis ({len(disponiveis)})")
    for forn in sorted(disponiveis, key=lambda f: f["nome"] or ""):
        status = forn.get("status_qualificacao") or "—"
        icone  = "✅" if status.lower() == "qualificado" else "🔶"
        with st.expander(f"{icone} {forn['nome']} · {status}"):
            col1, col2 = st.columns(2)
            with col1:
                st.write(f"**CNPJ:** {forn.get('cnpj') or '—'}")
                st.write(f"**Representante:** {forn.get('representante_legal') or '—'}")
                st.write(f"**CPF:** {forn.get('cpf') or '—'}")
                st.write(f"**RG:** {forn.get('rg') or '—'}")
            with col2:
                st.write(f"**E-mail:** {forn.get('email') or '—'}")
                st.write(f"**Telefone:** {forn.get('telefone') or '—'}")
                st.write(f"**Validade:** {forn.get('validade_qualificacao') or '—'}")
            st.text_area("Endereço:", value=forn.get("endereco") or "", disabled=True,
                         height=55, key=f"ef_{forn['id']}")

    st.divider()
    if bloqueados:
        st.subheader(f"Bloqueados / Não Qualificados ({len(bloqueados)})")
        for forn in sorted(bloqueados, key=lambda f: f["nome"] or ""):
            with st.expander(f"🚫 {forn['nome']}"):
                st.write(f"**CNPJ:** {forn.get('cnpj') or '—'}")
                st.write(f"**Status:** {forn.get('status_qualificacao') or '—'}")

    st.divider()
    if st.button("🔄 Recarregar dados do Notion"):
        st.cache_data.clear()
        st.rerun()
