import streamlit as st
import json
import subprocess
import os
import sys
import tempfile
import re
from datetime import date, datetime
from io import BytesIO
import zipfile

try:
    from notion_client import Client
    from notion_client.errors import APIResponseError
except ImportError:
    st.error("Biblioteca `notion-client` não instalada. Execute: pip install notion-client")
    st.stop()

# ============================================================================
# CONFIG
# ============================================================================

NOTION_TOKEN = st.secrets.get("NOTION_TOKEN") or os.getenv("NOTION_TOKEN")
DATABASE_ID  = st.secrets.get("DATABASE_ID")  or os.getenv("DATABASE_ID")

if not NOTION_TOKEN or not DATABASE_ID:
    st.error("""
    ❌ Token ou Database ID não configurados.

    **Streamlit Cloud:**
    1. Settings → Secrets no painel do app
    2. Adicione:
    ```
    NOTION_TOKEN = "ntn_..."
    DATABASE_ID  = "35f3a59..."
    ```

    **Local:**
    Crie `.streamlit/secrets.toml` com as mesmas chaves.
    """)
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
    body { font-family: 'Inter', -apple-system, sans-serif; }
    .main { padding: 2rem; }
    h1    { color: #1f5c4d; margin-bottom: 0.5rem; }
    .metadata { font-size: 0.85rem; color: #666; margin-bottom: 1.5rem; }
    .warning  { padding: 1rem; background: #f7e7e2; border-radius: 8px; border-left: 4px solid #b4472f; }
    .success  { padding: 1rem; background: #e6efe9; border-radius: 8px; border-left: 4px solid #1f5c4d; }
    .card     { background: #fffdf8; border: 1px solid #d8d4ca; border-radius: 10px; padding: 1.5rem; margin-bottom: 1rem; }
</style>
""", unsafe_allow_html=True)

# ============================================================================
# FUNÇÕES AUXILIARES
# ============================================================================

def _get_prop(props, name, prop_type="rich_text"):
    """Extrai um campo de propriedades do Notion de forma segura."""
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
        elif prop_type == "multi_select":
            return [item["name"] for item in prop.get("multi_select", [])]
        elif prop_type == "date":
            val = prop.get("date") or {}
            return val.get("start")
        elif prop_type == "checkbox":
            return prop.get("checkbox", False)
    except Exception:
        return None
    return None


def _page_to_fornecedor(page):
    props = page["properties"]
    G = lambda name, t="rich_text": _get_prop(props, name, t)
    return {
        "id":                    page["id"],
        "nome":                  G("Nome Empresa", "title") or G("PROJETISTA"),
        "responsavel_tecnico":   G("Nome do Responsável Técnico"),
        "projeta_bim":           G("Projeta em BIM?", "checkbox"),
        "data_cadastro":         G("Data de Cadastro", "date"),
        "status_qualificacao":   G("Status da Qualificação", "select"),
        "data_qualificacao":     G("Data de Qualificação", "date"),
        "validade_qualificacao": G("Validade da Qualificação", "date"),
        "status_geral":          G("Status Geral", "select"),
        "crea_cau":              G("CREA/CAU"),
        "cnpj":                  G("CNPJ"),
        "email":                 G("E-mail"),
        "telefone":              G("Telefone"),
        "contato":               G("CONTATO"),
        "razao_social":          G("RAZÃO SOCIAL"),
        "representante_legal":   G("REPRESENTANTE LEGAL"),
        "cpf":                   G("CPF"),
        "rg":                    G("RG"),
        "endereco":              G("ENDEREÇO"),
        "disciplinas":           G("DISCIPLINA", "multi_select"),
        "status_documento":      G("Status do Documento", "select"),
        "referencias":           G("Referências"),
        "estrutura_empresa":     G("Estrutura da Empresa"),
        "portfolio":             G("Portfólio, RG/CPF Comprovante"),
    }


@st.cache_data(ttl=3600)
def puxar_fornecedores():
    """
    Puxa todos os fornecedores do Notion com paginação segura.
    Exibe progresso na tela para que o app nunca pareça travado.
    """
    MAX_PAGES = 50   # proteção: no máximo 50 × 100 = 5 000 registros
    results   = []
    cursor    = None
    pagina    = 0

    progresso = st.empty()

    try:
        while pagina < MAX_PAGES:
            pagina += 1
            progresso.caption(f"⏳ Carregando página {pagina} do Notion…")

            params = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor

            try:
                resp = notion.databases.query(DATABASE_ID, **params)
            except APIResponseError as e:
                progresso.empty()
                st.error(f"Erro na API do Notion: {e}")
                return []

            batch = resp.get("results", [])
            results.extend(batch)

            has_more   = resp.get("has_more", False)
            cursor     = resp.get("next_cursor")

            # Sai quando não há mais páginas OU quando o cursor é nulo
            if not has_more or not cursor:
                break

        progresso.empty()

    except Exception as e:
        progresso.empty()
        st.error(f"Erro inesperado ao conectar com Notion: {e}")
        return []

    fornecedores = []
    for page in results:
        try:
            forn = _page_to_fornecedor(page)
            if forn["nome"]:
                fornecedores.append(forn)
        except Exception:
            pass   # registro malformado: ignora sem travar

    return fornecedores


def _validade_ok(data_str):
    """Retorna True se a data de validade ainda não expirou."""
    if not data_str:
        return False
    try:
        return datetime.strptime(data_str[:10], "%Y-%m-%d").date() >= date.today()
    except ValueError:
        return False


def formata_qualificacao(forn):
    """Monta a qualificação jurídica do fornecedor para o contrato."""
    razao    = forn.get("razao_social") or forn.get("nome", "")
    endereco = forn.get("endereco")     or "[ENDEREÇO A INFORMAR]"
    cnpj     = forn.get("cnpj")         or "[CNPJ A INFORMAR]"
    rep      = forn.get("representante_legal") or "[REPRESENTANTE A INFORMAR]"
    rg       = forn.get("rg")           or "[RG A INFORMAR]"
    cpf      = forn.get("cpf")          or "[CPF A INFORMAR]"

    return (
        f", com sede em {endereco}, inscrita no CNPJ sob o número {cnpj}, "
        f"neste ato representada legalmente e na forma estatutária pelo(a) "
        f"senhor(a) {rep}, portador(a) do RG {rg} e CPF {cpf};"
    )


# --------------------------------------------------------------------------
# Valor por extenso — versão completa em PT-BR, suporta centavos
# --------------------------------------------------------------------------

_UNIDADES   = ["", "um", "dois", "três", "quatro", "cinco", "seis", "sete",
                "oito", "nove", "dez", "onze", "doze", "treze", "quatorze",
                "quinze", "dezesseis", "dezessete", "dezoito", "dezenove"]
_DEZENAS    = ["", "", "vinte", "trinta", "quarenta", "cinquenta",
                "sessenta", "setenta", "oitenta", "noventa"]
_CENTENAS   = ["", "cem", "duzentos", "trezentos", "quatrocentos", "quinhentos",
                "seiscentos", "setecentos", "oitocentos", "novecentos"]


def _centenas_ext(n: int) -> str:
    """Converte inteiro 1–999 por extenso."""
    if n == 100:
        return "cem"
    c, resto = divmod(n, 100)
    d, u     = divmod(resto, 10)
    partes   = []
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


def _grupo(n: int, singular: str, plural: str) -> str:
    return f"{_centenas_ext(n)} {plural if n != 1 else singular}"


def numero_extenso(valor_str: str) -> str:
    """Converte uma string de valor BRL para extenso em PT-BR.

    Exemplos:
        "132.000,00"  → "cento e trinta e dois mil reais"
        "1.500,50"    → "um mil e quinhentos reais e cinquenta centavos"
        "100,01"      → "cem reais e um centavo"
    """
    try:
        limpo = str(valor_str).replace("R$", "").replace(" ", "")
        # normaliza: ponto de milhar + vírgula decimal  →  int_str, dec_str
        if "," in limpo:
            partes = limpo.replace(".", "").split(",")
            int_str, dec_str = partes[0], (partes[1] + "00")[:2]
        else:
            int_str, dec_str = limpo.replace(".", ""), "00"

        inteiro  = int(int_str)
        centavos = int(dec_str)

        if inteiro == 0 and centavos == 0:
            return "zero"

        partes_ext = []

        if inteiro > 0:
            bilhoes, resto = divmod(inteiro, 1_000_000_000)
            milhoes, resto = divmod(resto,   1_000_000)
            mil,     resto = divmod(resto,   1_000)
            unid           = resto

            if bilhoes:
                partes_ext.append(_grupo(bilhoes, "bilhão", "bilhões"))
            if milhoes:
                partes_ext.append(_grupo(milhoes, "milhão", "milhões"))
            if mil:
                partes_ext.append(f"{_centenas_ext(mil)} mil")
            if unid:
                partes_ext.append(_centenas_ext(unid))

            reais_ext = " e ".join(partes_ext)
            reais_ext += " real" if inteiro == 1 else " reais"
        else:
            reais_ext = ""

        if centavos > 0:
            cent_ext = _centenas_ext(centavos)
            cent_ext += " centavo" if centavos == 1 else " centavos"
        else:
            cent_ext = ""

        if reais_ext and cent_ext:
            return f"{reais_ext} e {cent_ext}"
        return reais_ext or cent_ext

    except Exception:
        return "[VALOR POR EXTENSO — CONFERIR]"


# --------------------------------------------------------------------------
# Chamada ao script gerador
# --------------------------------------------------------------------------

def _localizar_script():
    """
    Procura preencher_contrato.py em locais prováveis.
    Retorna o caminho absoluto ou None.
    """
    candidatos = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "preencher_contrato.py"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts", "preencher_contrato.py"),
        "preencher_contrato.py",
    ]
    for c in candidatos:
        if os.path.exists(c):
            return c
    return None


def _localizar_assets():
    """
    Procura o diretório assets (que contém template_contrato.docx,
    row_first.xml, row_cont.xml) em locais prováveis.
    """
    base = os.path.dirname(os.path.abspath(__file__))
    candidatos = [
        os.path.join(base, "assets"),
        base,
        os.path.join(base, "scripts"),
    ]
    for c in candidatos:
        if os.path.isfile(os.path.join(c, "template_contrato.docx")):
            return c
    return None


def gerar_docx(dados: dict) -> tuple[bytes | None, str]:
    """
    Chama preencher_contrato.py e devolve (bytes_do_docx, mensagem_erro).
    Em caso de sucesso, mensagem_erro é string vazia.
    """
    script = _localizar_script()
    if script is None:
        return None, (
            "preencher_contrato.py não encontrado. Coloque-o na raiz do repositório "
            "ou em ./scripts/."
        )

    assets = _localizar_assets()
    if assets is None:
        return None, (
            "Pasta de assets não encontrada. Certifique-se de que template_contrato.docx, "
            "row_first.xml e row_cont.xml estão em ./assets/ (ou na raiz do repositório)."
        )

    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as fj:
            json.dump(dados, fj, ensure_ascii=False)
            json_path = fj.name

        docx_path = tempfile.mktemp(suffix=".docx")

        result = subprocess.run(
            [sys.executable, script, json_path, docx_path,
             "--skill-dir", os.path.dirname(assets)],
            capture_output=True,
            text=True,
            timeout=60,
        )

        if result.returncode == 0 and os.path.exists(docx_path):
            with open(docx_path, "rb") as fd:
                conteudo = fd.read()
            return conteudo, ""
        else:
            return None, result.stderr or "Erro desconhecido ao rodar preencher_contrato.py"

    except subprocess.TimeoutExpired:
        return None, "Timeout ao gerar o contrato (>60 s)."
    except Exception as e:
        return None, str(e)
    finally:
        for p in (json_path, docx_path):
            try:
                if os.path.exists(p):
                    os.unlink(p)
            except Exception:
                pass


# ============================================================================
# UI
# ============================================================================

st.title("VICTA · Montador de Contratos")
st.markdown("Gere contratos de forma rápida e confiável.")

# --------------------------------------------------------------------------
# Carregar fornecedores
# --------------------------------------------------------------------------

with st.spinner("Conectando ao Notion…"):
    fornecedores = puxar_fornecedores()

if not fornecedores:
    st.error("Nenhum fornecedor encontrado — verifique o token e o Database ID.")
    st.stop()

# Separar aptos (qualificado + validade vigente) e bloqueados
aptos = [
    f for f in fornecedores
    if (f.get("status_qualificacao") or "").strip().lower() == "qualificado"
    and _validade_ok(f.get("validade_qualificacao"))
]
aptos_sem_validade = [
    f for f in fornecedores
    if (f.get("status_qualificacao") or "").strip().lower() == "qualificado"
    and not _validade_ok(f.get("validade_qualificacao"))
]
bloqueados = [
    f for f in fornecedores
    if (f.get("status_qualificacao") or "").strip().lower() != "qualificado"
]

st.markdown(f"""
<div class="metadata">
🔄 Cadastro sincronizado do Notion ·
{len(aptos)} aptos · {len(aptos_sem_validade)} com validade expirada · {len(bloqueados)} bloqueados
</div>
""", unsafe_allow_html=True)

# --------------------------------------------------------------------------
# Tabs
# --------------------------------------------------------------------------

tab_contrato, tab_inspecionar = st.tabs(["📝 Novo Contrato", "🔍 Inspecionar Dados"])

# ============================================================================
# ABA 1: GERAR CONTRATO
# ============================================================================

with tab_contrato:

    # --- 1. Partes ---
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("1. Partes do Contrato")

        nomes_aptos = sorted(f["nome"] for f in aptos)
        nome_spe = st.selectbox(
            "SPE / Contratante",
            options=nomes_aptos,
            help="Apenas cadastros com qualificação vigente aparecem aqui.",
        )
        spe = next((f for f in aptos if f["nome"] == nome_spe), None)

        if spe:
            val = spe.get("validade_qualificacao") or "—"
            st.info(f"**{spe['nome']}**  \nCNPJ: {spe.get('cnpj') or '—'}  \nValidade: {val}")

        # Campos com informação faltando
        if spe:
            faltando_spe = [c for c in ("cnpj", "endereco", "representante_legal", "cpf", "rg")
                            if not spe.get(c)]
            if faltando_spe:
                st.warning(f"⚠️ Dados incompletos na SPE: {', '.join(faltando_spe)}")

    with col2:
        st.subheader("2. Tipo de Documento")
        tipo = st.radio(
            "Tipo",
            ["BIM", "P2D", "DISTRATO"],
            horizontal=True,
            help="BIM inclui Anexo 3 (BIM); P2D termina no Anexo 2.",
        )

        if tipo != "DISTRATO":
            nome_proj = st.selectbox(
                "Projetista / Contratada",
                options=["— selecione —"] + nomes_aptos,
                key="proj",
            )
            proj = next((f for f in aptos if f["nome"] == nome_proj), None)

            if proj:
                val = proj.get("validade_qualificacao") or "—"
                st.info(f"**{proj['nome']}**  \nCNPJ: {proj.get('cnpj') or '—'}  \nValidade: {val}")
                faltando_proj = [c for c in ("cnpj", "endereco", "representante_legal", "cpf", "rg")
                                 if not proj.get(c)]
                if faltando_proj:
                    st.warning(f"⚠️ Dados incompletos no projetista: {', '.join(faltando_proj)}")
        else:
            proj = None

    st.divider()

    # --- 2. Dados do contrato ---
    col1, col2 = st.columns(2)
    with col1:
        num_contrato  = st.text_input("Nº do contrato", placeholder="2026.09.09")
        data_contrato = st.date_input("Data", value=date.today())
    with col2:
        if tipo == "DISTRATO":
            data_original = st.date_input("Data do contrato original", value=date.today())
        objeto = st.text_input("Objeto/Serviço", placeholder="PROJETO EXECUTIVO DE ARQUITETURA – BIM")

    obra  = st.text_input("Obra", placeholder="Nome do empreendimento")
    local = st.text_area("Local da obra", placeholder="Endereço completo", height=60)

    valor_total_str = st.text_input("Valor total (R$)", placeholder="132.000,00")
    try:
        valor_total = float(
            valor_total_str.replace("R$", "").replace(".", "").replace(",", ".").strip()
        )
    except Exception:
        valor_total = 0.0

    if valor_total > 0:
        extenso = numero_extenso(valor_total_str)
        st.markdown(f"**Por extenso:** {extenso}")

    st.divider()

    # --- 3. Parcelas ---
    st.subheader("3. Parcelas")
    num_parcelas = st.number_input("Quantas parcelas?", min_value=1, max_value=10, value=3)

    parcelas = []
    for i in range(int(num_parcelas)):
        c1, c2, c3 = st.columns(3)
        with c1:
            pct = st.number_input(
                f"Parcela {i+1} (%)", min_value=0.0, max_value=100.0,
                value=round(100.0 / num_parcelas, 2), key=f"pct_{i}",
            )
        with c2:
            etapa = st.text_input(f"Etapa {i+1}", value=f"Parcela {i+1}", key=f"etapa_{i}")
        with c3:
            valor_parc = st.text_input(
                f"Valor {i+1} (R$)", value="", placeholder="calculado automaticamente",
                key=f"valor_{i}",
            )
        parcelas.append({
            "pct":   f"{pct:.2f}%".replace(".", ","),
            "etapa": etapa,
            "valor": valor_parc,
        })

    soma_pct = sum(
        float(p["pct"].replace("%", "").replace(",", ".")) for p in parcelas
    )
    if abs(soma_pct - 100.0) > 0.1:
        st.warning(f"⚠️ Percentuais somam {soma_pct:.2f}%, não 100 %")

    st.divider()

    # --- 4. Escopo ---
    st.subheader("4. Escopo (Anexo 1)")
    num_itens = st.number_input("Quantos itens de escopo?", min_value=1, max_value=10, value=3)

    escopo = []
    for i in range(int(num_itens)):
        item = st.text_input(f"Item {i+1}", key=f"escopo_{i}")
        if item:
            escopo.append(item)

    if len(escopo) > 3:
        st.info(f"✓ {len(escopo)} itens — o parágrafo do 3º item será clonado para os extras.")

    st.divider()

    # --- Botão gerar ---
    if st.button("Gerar Contrato", type="primary", use_container_width=True):

        erros = []
        if not spe:
            erros.append("Escolha uma SPE")
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
                    "nome":          spe["nome"],
                    "qualificacao":  formata_qualificacao(spe),
                },
                "contratada": {
                    "nome":         proj["nome"] if proj else "—",
                    "qualificacao": formata_qualificacao(proj) if proj else "",
                },
                "obra":               obra or spe["nome"],
                "servico":            objeto,
                "objeto_nome":        objeto,
                "objeto_complemento": (
                    f", para a obra {obra or spe['nome']}, situada na {local}."
                    if local else "."
                ),
                "valor_num":     valor_total_str,
                "valor_extenso": numero_extenso(valor_total_str),
                "escopo":        escopo,
                "parcelas":      parcelas,
            }

            with st.spinner("Gerando contrato…"):
                docx_bytes, erro_msg = gerar_docx(dados)

            if docx_bytes:
                st.success("✓ Contrato gerado com sucesso!")
                nome_arquivo = f"Contrato_{tipo}_{num_contrato.replace('.', '-')}.docx"
                st.download_button(
                    label="📥 Baixar contrato (.docx)",
                    data=docx_bytes,
                    file_name=nome_arquivo,
                    mime=(
                        "application/vnd.openxmlformats-officedocument"
                        ".wordprocessingml.document"
                    ),
                    use_container_width=True,
                )
                st.download_button(
                    label="📋 Baixar dados (.json)",
                    data=json.dumps(dados, ensure_ascii=False, indent=2),
                    file_name=f"dados_{num_contrato.replace('.', '-')}.json",
                    mime="application/json",
                )
            else:
                st.error(f"Erro ao gerar contrato:\n{erro_msg}")

# ============================================================================
# ABA 2: INSPECIONAR DADOS
# ============================================================================

with tab_inspecionar:

    st.subheader(f"Fornecedores Aptos ({len(aptos)})")
    if aptos:
        for forn in sorted(aptos, key=lambda f: f["nome"] or ""):
            with st.expander(f"✅ {forn['nome']}", expanded=False):
                c1, c2 = st.columns(2)
                with c1:
                    st.write(f"**CNPJ**: {forn.get('cnpj') or '—'}")
                    st.write(f"**Representante**: {forn.get('representante_legal') or '—'}")
                    st.write(f"**CPF**: {forn.get('cpf') or '—'}")
                    st.write(f"**RG**: {forn.get('rg') or '—'}")
                with c2:
                    st.write(f"**E-mail**: {forn.get('email') or '—'}")
                    st.write(f"**Telefone**: {forn.get('telefone') or '—'}")
                    st.write(f"**Validade**: {forn.get('validade_qualificacao') or '—'}")
                st.text_area(
                    "Endereço:", value=forn.get("endereco") or "", disabled=True, height=60,
                    key=f"end_apto_{forn['id']}",
                )
    else:
        st.info("Nenhum fornecedor com qualificação vigente no momento.")

    st.divider()

    if aptos_sem_validade:
        st.subheader(f"Qualificados com Validade Expirada ({len(aptos_sem_validade)})")
        for forn in sorted(aptos_sem_validade, key=lambda f: f["nome"] or ""):
            val = forn.get("validade_qualificacao") or "—"
            with st.expander(f"⏰ {forn['nome']} · expirou em {val}", expanded=False):
                st.write(f"**CNPJ**: {forn.get('cnpj') or '—'}")
                st.write(f"**Validade**: {val}")
        st.divider()

    if bloqueados:
        st.subheader(f"Bloqueados / Não Qualificados ({len(bloqueados)})")
        for forn in sorted(bloqueados, key=lambda f: f["nome"] or ""):
            motivo = forn.get("status_qualificacao") or "status não informado"
            with st.expander(f"🚫 {forn['nome']} · {motivo}", expanded=False):
                st.write(f"**CNPJ**: {forn.get('cnpj') or '—'}")
                st.write(f"**Status**: {motivo}")
                st.write(f"**Validade**: {forn.get('validade_qualificacao') or '—'}")
    else:
        st.info("Todos os fornecedores estão qualificados! ✓")

    st.divider()
    if st.button("🔄 Recarregar dados do Notion"):
        st.cache_data.clear()
        st.rerun()
