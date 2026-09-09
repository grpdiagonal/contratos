import streamlit as st
import json
import subprocess
import os
import tempfile
import re
from datetime import date
from io import BytesIO
import zipfile

try:
    from notion_client import Client
except ImportError:
    st.error("Biblioteca `notion-client` não instalada. Execute: pip install notion-client")
    st.stop()

# ============================================================================
# CONFIG
# ============================================================================

NOTION_TOKEN = st.secrets.get("NOTION_TOKEN") or os.getenv("NOTION_TOKEN")
DATABASE_ID = st.secrets.get("DATABASE_ID") or os.getenv("DATABASE_ID")

if not NOTION_TOKEN or not DATABASE_ID:
    st.error("""
    ❌ Token ou Database ID não configurados.
    
    **Streamlit Cloud:**
    1. Vá para o repositório do projeto no GitHub
    2. Settings → Secrets
    3. Adicione:
       ```
       NOTION_TOKEN = ntn_...
       DATABASE_ID = 35f3a59...
       ```
    
    **Local (desenvolvimento):**
    Crie um arquivo `.streamlit/secrets.toml`:
    ```
    NOTION_TOKEN = "ntn_..."
    DATABASE_ID = "35f3a59..."
    ```
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
    initial_sidebar_state="collapsed"
)

st.markdown("""
<style>
    body { font-family: 'Inter', -apple-system, sans-serif; }
    .main { padding: 2rem; }
    h1 { color: #1f5c4d; margin-bottom: 0.5rem; }
    .metadata { font-size: 0.85rem; color: #666; margin-bottom: 1.5rem; }
    .warning { padding: 1rem; background: #f7e7e2; border-radius: 8px; border-left: 4px solid #b4472f; }
    .success { padding: 1rem; background: #e6efe9; border-radius: 8px; border-left: 4px solid #1f5c4d; }
    .card { background: #fffdf8; border: 1px solid #d8d4ca; border-radius: 10px; padding: 1.5rem; margin-bottom: 1rem; }
</style>
""", unsafe_allow_html=True)

# ============================================================================
# FUNÇÕES
# ============================================================================

@st.cache_data(ttl=3600)
def puxar_fornecedores():
    """Puxa todos os fornecedores do Notion."""
    try:
        results = []
        cursor = None
        
        while True:
            query_params = {"page_size": 100}
            if cursor:
                query_params["start_cursor"] = cursor
            
            response = notion.databases.query(DATABASE_ID, **query_params)
            results.extend(response["results"])
            
            cursor = response.get("next_cursor")
            if not cursor:
                break
        
        fornecedores = []
        for page in results:
            props = page["properties"]
            
            def get_prop(name, prop_type="rich_text"):
                if name not in props:
                    return None
                prop = props[name]
                
                if prop_type == "title":
                    return "".join(t["plain_text"] for t in prop.get("title", []))
                elif prop_type == "rich_text":
                    return "".join(t["plain_text"] for t in prop.get("rich_text", []))
                elif prop_type == "select":
                    return prop.get("select", {}).get("name")
                elif prop_type == "multi_select":
                    return [item["name"] for item in prop.get("multi_select", [])]
                elif prop_type == "date":
                    val = prop.get("date", {})
                    return val.get("start") if val else None
                elif prop_type == "checkbox":
                    return prop.get("checkbox", False)
                return None
            
            fornecedor = {
                "id": page["id"],
                "nome": get_prop("Nome Empresa", "title") or get_prop("PROJETISTA", "rich_text"),
                "responsavel_tecnico": get_prop("Nome do Responsável Técnico", "rich_text"),
                "projeta_bim": get_prop("Projeta em BIM?", "checkbox"),
                "data_cadastro": get_prop("Data de Cadastro", "date"),
                "status_qualificacao": get_prop("Status da Qualificação", "select"),
                "data_qualificacao": get_prop("Data de Qualificação", "date"),
                "validade_qualificacao": get_prop("Validade da Qualificação", "date"),
                "status_geral": get_prop("Status Geral", "select"),
                "crea_cau": get_prop("CREA/CAU", "rich_text"),
                "cnpj": get_prop("CNPJ", "rich_text"),
                "email": get_prop("E-mail", "rich_text"),
                "telefone": get_prop("Telefone", "rich_text"),
                "contato": get_prop("CONTATO", "rich_text"),
                "razao_social": get_prop("RAZÃO SOCIAL", "rich_text"),
                "representante_legal": get_prop("REPRESENTANTE LEGAL", "rich_text"),
                "cpf": get_prop("CPF", "rich_text"),
                "rg": get_prop("RG", "rich_text"),
                "endereco": get_prop("ENDEREÇO", "rich_text"),
                "disciplinas": get_prop("DISCIPLINA", "multi_select"),
                "status_documento": get_prop("Status do Documento", "select"),
                "referencias": get_prop("Referências", "rich_text"),
                "estrutura_empresa": get_prop("Estrutura da Empresa", "rich_text"),
                "portfolio": get_prop("Portfólio, RG/CPF Comprovante", "rich_text"),
            }
            
            if fornecedor["nome"]:  # só adiciona se tem nome
                fornecedores.append(fornecedor)
        
        return fornecedores
    except Exception as e:
        st.error(f"Erro ao conectar com Notion: {str(e)}")
        return []

def formata_qualificacao(forn):
    """Monta a qualificação jurídica do fornecedor."""
    razao = forn.get("razao_social") or forn.get("nome", "")
    endereco = forn.get("endereco", "[ENDEREÇO A INFORMAR]")
    cnpj = forn.get("cnpj", "[CNPJ A INFORMAR]")
    rep = forn.get("representante_legal", "[REPRESENTANTE A INFORMAR]")
    rg = forn.get("rg", "[RG A INFORMAR]")
    cpf = forn.get("cpf", "[CPF A INFORMAR]")
    
    return (
        f", com sede em {endereco}, inscrita no CNPJ sob o número {cnpj}, "
        f"neste ato representada legalmente e na forma estatutária pelo(a) "
        f"senhor(a) {rep}, portador(a) do RG {rg} e CPF {cpf};"
    )

def numero_extenso(valor_str):
    """Converte valor em BRL para extenso."""
    # Simplified version — a versão completa está no painel HTML
    try:
        v = float(valor_str.replace("R$", "").replace(".", "").replace(",", ".").strip())
        if v == 0:
            return "zero"
        # Aqui você pode usar num2words ou a função do painel
        # Para agora, retorna uma simplificação
        milhoes = int(v // 1_000_000)
        mil = int((v % 1_000_000) // 1_000)
        resto = int(v % 1_000)
        
        partes = []
        if milhoes:
            partes.append(f"{milhoes} milhão{'es' if milhoes > 1 else ''}")
        if mil:
            partes.append(f"{mil} mil")
        if resto:
            partes.append(f"{resto} reais")
        
        return " e ".join(partes) if partes else "zero"
    except:
        return "[VALOR POR EXTENSO A CONFERIR]"

# ============================================================================
# UI
# ============================================================================

st.title("VICTA · Montador de Contratos")
st.markdown("Gere contratos de forma rápida e confiável.")

# Carregar fornecedores
with st.spinner("Carregando cadastro do Notion..."):
    fornecedores = puxar_fornecedores()

if not fornecedores:
    st.error("Nenhum fornecedor encontrado na base Notion.")
    st.stop()

# Separar aptos e bloqueados
aptos = [f for f in fornecedores 
         if f.get("status_qualificacao", "").lower() == "qualificado"]
bloqueados = [f for f in fornecedores 
              if f.get("status_qualificacao", "").lower() != "qualificado"]

st.markdown(f"""
<div class="metadata">
🔄 Cadastro sincronizado do Notion · {len(aptos)} fornecedores aptos, {len(bloqueados)} bloqueados
</div>
""", unsafe_allow_html=True)

# Tabs
tab_contrato, tab_inspeccionar = st.tabs(["📝 Novo Contrato", "🔍 Inspecionar Dados"])

# ============================================================================
# ABA 1: GERAR CONTRATO
# ============================================================================

with tab_contrato:
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("1. Partes do Contrato")
        
        nome_spe = st.selectbox(
            "SPE / Contratante",
            options=[f["nome"] for f in aptos],
            key="spe",
            help="Apenas clientes com status qualificado aparecem aqui"
        )
        spe = next((f for f in aptos if f["nome"] == nome_spe), None)
        
        if spe:
            st.info(f"""
            **{spe['nome']}**
            CNPJ: {spe.get('cnpj', '—')}
            """)
    
    with col2:
        st.subheader("2. Tipo de Documento")
        
        tipo = st.radio(
            "Escolha o tipo",
            ["BIM", "P2D", "DISTRATO"],
            horizontal=True,
            help="BIM inclui Anexo 3; P2D termina no Anexo 2"
        )
        
        if tipo != "DISTRATO":
            nome_proj = st.selectbox(
                "Projetista / Contratada",
                options=[f["nome"] for f in aptos],
                key="proj",
                help="Apenas fornecedores qualificados"
            )
            proj = next((f for f in aptos if f["nome"] == nome_proj), None)
            
            if proj:
                st.info(f"""
                **{proj['nome']}**
                CNPJ: {proj.get('cnpj', '—')}
                Validade: {proj.get('validade_qualificacao', '—')}
                """)
    
    st.divider()
    
    col1, col2 = st.columns(2)
    with col1:
        num_contrato = st.text_input("Nº do contrato", placeholder="2026.09.09")
        data_contrato = st.date_input("Data", value=date.today())
    with col2:
        if tipo == "DISTRATO":
            data_original = st.date_input("Data do contrato original", value=date.today())
        
        objeto = st.text_input("Objeto/Serviço", placeholder="PROJETO EXECUTIVO DE ARQUITETURA – BIM")
    
    obra = st.text_input("Obra", placeholder="Nome do empreendimento")
    local = st.text_area("Local da obra", placeholder="Endereço completo", height=60)
    valor_total_str = st.text_input("Valor total (R$)", placeholder="132.000,00")
    
    try:
        valor_total = float(valor_total_str.replace("R$", "").replace(".", "").replace(",", ".").strip())
    except:
        valor_total = 0.0
    
    if valor_total > 0:
        st.markdown(f"**Por extenso:** {numero_extenso(valor_total_str)} reais")
    
    st.divider()
    
    st.subheader("3. Parcelas")
    
    num_parcelas = st.number_input("Quantas parcelas?", min_value=1, max_value=10, value=3)
    
    parcelas = []
    for i in range(num_parcelas):
        col1, col2, col3 = st.columns(3)
        with col1:
            pct = st.number_input(f"Parcela {i+1} (%)", min_value=0.0, max_value=100.0, value=100.0/num_parcelas)
        with col2:
            etapa = st.text_input(f"Etapa {i+1}", value=f"Parcela {i+1}", key=f"etapa_{i}")
        with col3:
            valor_parc = st.text_input(f"Valor {i+1} (R$)", value="", placeholder="auto", key=f"valor_{i}")
        
        parcelas.append({
            "pct": f"{pct:.2f}%".replace(".", ","),
            "etapa": etapa,
            "valor": valor_parc
        })
    
    soma_pct = sum(float(p["pct"].replace("%", "").replace(",", ".")) for p in parcelas)
    if abs(soma_pct - 100.0) > 0.1:
        st.warning(f"⚠️ Percentuais somam {soma_pct:.1f}%, não 100%")
    
    st.divider()
    
    st.subheader("4. Escopo (Anexo 1)")
    
    num_itens = st.number_input("Quantos itens de escopo?", min_value=1, max_value=10, value=3)
    
    escopo = []
    for i in range(num_itens):
        item = st.text_input(f"Item {i+1}", placeholder=f"Descrição do item", key=f"escopo_{i}")
        if item:
            escopo.append(item)
    
    if len(escopo) > 3:
        st.info(f"✓ Com {len(escopo)} itens, o Anexo 1 vai ter espaço extra.")
    
    st.divider()
    
    # Botão de gerar
    if st.button("Gerar Contrato", type="primary", use_container_width=True):
        
        # Validações
        erros = []
        if not spe:
            erros.append("Escolha uma SPE")
        if tipo != "DISTRATO" and not proj:
            erros.append("Escolha um projetista")
        if not objeto:
            erros.append("Informe o objeto/serviço")
        if valor_total == 0:
            erros.append("Informe o valor total")
        if not escopo:
            erros.append("Informe pelo menos um item de escopo")
        if abs(soma_pct - 100.0) > 0.1:
            erros.append("Percentuais não somam 100%")
        
        if erros:
            st.error("Não é possível gerar:\n" + "\n".join(f"• {e}" for e in erros))
        else:
            # Montar JSON
            dados = {
                "tipo": tipo,
                "num_contrato": num_contrato,
                "data": data_contrato.strftime("%d/%m/%Y"),
                "contratante": {
                    "nome": spe["nome"],
                    "qualificacao": formata_qualificacao(spe)
                },
                "contratada": {
                    "nome": proj["nome"] if proj else "—",
                    "qualificacao": formata_qualificacao(proj) if proj else ""
                },
                "obra": obra or spe["nome"],
                "servico": objeto,
                "objeto_nome": objeto,
                "objeto_complemento": f", para a obra {obra or spe['nome']}, situada na {local}." if local else ".",
                "valor_num": valor_total_str,
                "valor_extenso": numero_extenso(valor_total_str),
                "escopo": escopo,
                "parcelas": parcelas,
            }
            
            # Tentar gerar o .docx
            try:
                with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as f:
                    json.dump(dados, f)
                    json_path = f.name
                
                docx_path = tempfile.mktemp(suffix='.docx')
                
                # Chamar preencher_contrato.py
                # (você precisa ter esse script em ./scripts/ ou no PATH)
                result = subprocess.run(
                    ["python3", "-m", "scripts.preencher_contrato", json_path, docx_path],
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                
                if result.returncode == 0 and os.path.exists(docx_path):
                    with open(docx_path, 'rb') as f:
                        docx_bytes = f.read()
                    
                    st.success("✓ Contrato gerado com sucesso!")
                    st.download_button(
                        label="📥 Baixar contrato (.docx)",
                        data=docx_bytes,
                        file_name=f"Contrato_{tipo}_{num_contrato.replace('.', '-')}.docx",
                        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        use_container_width=True
                    )
                    
                    # JSON também
                    st.download_button(
                        label="📋 Baixar dados (.json)",
                        data=json.dumps(dados, ensure_ascii=False, indent=2),
                        file_name=f"dados_contrato_{num_contrato.replace('.', '-')}.json",
                        mime="application/json"
                    )
                else:
                    st.error(f"Erro ao gerar contrato:\n{result.stderr}")
                
                # Cleanup
                if os.path.exists(json_path):
                    os.unlink(json_path)
                if os.path.exists(docx_path):
                    os.unlink(docx_path)
            
            except Exception as e:
                st.error(f"Erro: {str(e)}")

# ============================================================================
# ABA 2: INSPECIONAR DADOS
# ============================================================================

with tab_inspeccionar:
    st.subheader("Fornecedores Aptos")
    
    if aptos:
        for forn in sorted(aptos, key=lambda f: f["nome"] or ""):
            with st.expander(f"✅ {forn['nome']}", expanded=False):
                col1, col2 = st.columns(2)
                with col1:
                    st.write(f"**CNPJ**: {forn.get('cnpj', '—')}")
                    st.write(f"**Representante**: {forn.get('representante_legal', '—')}")
                    st.write(f"**CPF**: {forn.get('cpf', '—')}")
                    st.write(f"**RG**: {forn.get('rg', '—')}")
                with col2:
                    st.write(f"**E-mail**: {forn.get('email', '—')}")
                    st.write(f"**Telefone**: {forn.get('telefone', '—')}")
                    st.write(f"**Validade**: {forn.get('validade_qualificacao', '—')}")
                
                st.text_area("Endereço:", value=forn.get("endereco", ""), disabled=True, height=60)
    
    st.divider()
    
    if bloqueados:
        st.subheader(f"Fornecedores Bloqueados ({len(bloqueados)})")
        
        for forn in sorted(bloqueados, key=lambda f: f["nome"] or ""):
            motivo = f"Status: {forn.get('status_qualificacao', 'não informado')}"
            with st.expander(f"🚫 {forn['nome']} · {motivo}", expanded=False):
                st.write(f"**CNPJ**: {forn.get('cnpj', '—')}")
                st.write(f"**Status da Qualificação**: {forn.get('status_qualificacao', '—')}")
                st.write(f"**Validade**: {forn.get('validade_qualificacao', '—')}")
    
    else:
        st.info("Todos os fornecedores estão qualificados! ✓")
