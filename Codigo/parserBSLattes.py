import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, unquote, urlparse

from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)

RE_NUMERO_PRODUCOES = r"N.{0,5}mero de produ.{0,5}es|Numero de producoes"


def _normalize_whitespace(text: Optional[str]) -> str:
    """
    Normaliza espaços em branco consecutivos para um único espaço.
    """
    return re.sub(r"\s+", " ", (text or ""))


def _extract_lattes_id(href: Optional[str]) -> str:
    """
    Extrai o ID Lattes de uma URL ou string de consulta
    Retorna o ID de 16 digitos ou string vazia se não encontrado
    """
    if not href:
        return ""

    match = re.search(
        r"(?:http[s]?://)?(?:www\.)?lattes\.cnpq\.br/(\d{16})",
        href,
    )
    if match:
        return match.group(1)

    match = re.search(r"id=(\d{16})", href)
    if match:
        return match.group(1)

    return ""

# --------------------------------------------------------------------------- #
# Classe ParserBSLattes
# --------------------------------------------------------------------------- #

class ParserBSLattes:
    def __init__(self, id_lattes: str, cv_lattes_html: str) -> None:
        # Metadados
        self.idLattes = id_lattes
        self.nomeCompleto: str = ""
        self.listaNomesCitacao: List[str] = []
        self.areasAtuacao: List[Dict[str, str]] = []
        self.linhasPesquisa: List[str] = []
        self.sexo: str = ""
        self.genero: str = ""

        # Orientações
        self.listaOA: List[Dict[str, Any]] = []  # Em andamento
        self.listaOC: List[Dict[str, Any]] = []  # Concluidas

        # Projetos
        self.listaPP: List[Dict[str, Any]] = []

        # Produções
        self.listaPB: List[Dict[str, Any]] = []  # Bibliograficas
        self.listaPT: List[Dict[str, Any]] = []  # Tecnicas
        self.listaPA: List[Dict[str, Any]] = []  # Artisticas

        # Debug
        self.debug_parsing: bool = False
        self._debug_max_snippet: int = 500

        # Carrega o HTML inicial
        self._soup: Optional[BeautifulSoup] = None
        self.parse(cv_lattes_html)

    # ------------------------------------------------------------------ #
    # Auxiliares de depuração
    # ------------------------------------------------------------------ #
    
    def enable_debug(
        self,
        enable: bool = True,
        max_snippet: int = 500,
        level: int = logging.DEBUG,
        logger_name: Optional[str] = None,
        log_file: Optional[str] = None,
    ) -> None:
        """
        Configurar logs de depuração leves para o parser.

        - enable: ativar/desativar mensagens de depuração
        - max_snippet: comprimento maximo dos trechos HTML nos logs
        - level: nivel de logging a ser aplicado ao logger do parser
        - logger_name: nome opcional do logger (padrão e o logger do modulo)
        - log_file: caminho opcional para gravar logs em arquivo
        """
        
        self.debug_parsing = bool(enable)
        self._debug_max_snippet = max(int(max_snippet or 500), 1)

        log = logging.getLogger(logger_name) if logger_name else logger
        log.setLevel(level)

        # Evita considerar FileHandler como StreamHandler para manter saida no terminal.
        if not any(type(h) is logging.StreamHandler for h in log.handlers):
            handler = logging.StreamHandler()
            handler.setFormatter(
                logging.Formatter("%(levelname)s:%(name)s:%(message)s")
            )
            log.addHandler(handler)

        if log_file:
            has_same_file = False
            for h in log.handlers:
                if isinstance(h, logging.FileHandler):
                    try:
                        if h.baseFilename == log_file:
                            has_same_file = True
                            break
                    except Exception:
                        continue

            if not has_same_file:
                file_handler = logging.FileHandler(log_file, encoding="utf-8")
                file_handler.setFormatter(
                    logging.Formatter(
                        "%(asctime)s %(levelname)s:%(name)s:%(message)s"
                    )
                )
                file_handler.setLevel(level)
                log.addHandler(file_handler)
    
    def _snippet(self, node: Any) -> str:
        """
        Representação em string truncada com segurança de `node` para logs de depuração
        Retorna uma string de no maximo `self._debug_max_snippet`
        """
        if node is None:
            return ""

        try:
            text = str(node)
        except Exception:
            try:
                text = repr(node)
            except Exception:
                return ""

        max_len = self._debug_max_snippet
        if not isinstance(max_len, int) or max_len <= 0:
            max_len = 500

        return text[:max_len]

    def _trace(
        self,
        section: str,
        step: str,
        *,
        strategy: Optional[str] = None,
        fallback: bool = False,
        success: Optional[bool] = None,
        details: str = "",
    ) -> None:
        """
        Emite logs estruturados de tracing quando debug_parsing estiver ativo.

        Campos:
        - section: secao logica (ex.: projetos, biblio, tecnica, artistica, orientacoes)
        - step: operacao/metodo utilizado
        - strategy: nome da estrategia (primary/aux/fallback/regex etc.)
        - fallback: se esta estrategia eh fallback em relacao a anterior
        - success: resultado da estrategia (True/False/None)
        - details: contexto adicional curto
        """
        if not getattr(self, "debug_parsing", False):
            return

        status = "unknown"
        if success is True:
            status = "ok"
        elif success is False:
            status = "fail"

        strategy_text = strategy or "default"
        fallback_text = "yes" if fallback else "no"
        detail_text = details.strip()
        if detail_text:
            logger.debug(
                "TRACE section=%s step=%s strategy=%s fallback=%s status=%s details=%s",
                section,
                step,
                strategy_text,
                fallback_text,
                status,
                detail_text,
            )
        else:
            logger.debug(
                "TRACE section=%s step=%s strategy=%s fallback=%s status=%s",
                section,
                step,
                strategy_text,
                fallback_text,
                status,
            )

    # ------------------------------------------------------------------ #
    # API Publica
    # ------------------------------------------------------------------ #

    def parse(self, cvLattesHTML: str) -> None:
        try:
            soup = BeautifulSoup(cvLattesHTML, "html.parser")
        except Exception:
            logging.error("Erro ao abrir o arquivo HTML.", exc_info=True)
            soup = BeautifulSoup(str(cvLattesHTML), "html.parser")

        self._soup = soup

        # ------------------------------------------------------------------ #
        # Metadata (nome, nomes em citações)
        # ------------------------------------------------------------------ #
        h2_nome = soup.find("h2", class_="nome")
        if h2_nome:
            self.nomeCompleto = self._text_of_tag(h2_nome)

        try:
            cita_nomes = soup.find('b', string="Nome em citações bibliográficas")
            cita_nomes_pai = cita_nomes.find_parent('div', class_="layout-cell-3")
            cita_nomes_tag = cita_nomes_pai.find_next_sibling('div', class_="layout-cell-9")
            
            nomes_citacao = cita_nomes_tag.get_text(strip=True)
            self.listaNomesCitacao = [
                n.strip() for n in re.split(r";\s*", nomes_citacao) if n.strip()
            ]
        except Exception:
            pass

        try:
            areas_anchor = soup.find("a", attrs={"name": "AreasAtuacao"})
            if areas_anchor:
                areas_root = areas_anchor.find_next("div", class_="layout-cell layout-cell-12 data-cell")
                if areas_root:
                    self._parse_areas_atuacao_section(areas_root)
        except Exception:
            pass

        try:
            linhas_anchor = soup.find("a", attrs={"name": "LinhaPesquisa"})
            if linhas_anchor:
                linhas_root = linhas_anchor.find_next("div", class_="layout-cell layout-cell-12 data-cell")
                if linhas_root:
                    self._parse_linhas_pesquisa_section(linhas_root)
        except Exception:
            pass

        # ------------------------------------------------------------------ #
        # Manipuladores de seções (por nome de ancora)
        # ------------------------------------------------------------------ #
        
        def _handle_producoes(section_root: Tag):

            """
            Dentro desta, estão Produções Bibliograficas, Produções Tecnicas e
            Produções Artisticas. Todas com seus respectivos subtipos e são separadas
            por cabeçalhos.
            """
            self._trace("producoes", "_handle_producoes", strategy="primary", success=True)

            # Cada cabeçalho cita-artigos e seguido por um bloco de itens ate o
            # proximo cabeçalho/inst_back; usamos apenas irmãos diretos para não
            # duplicar a mesma arvore HTML.
            for header in section_root.find_all("div", class_="cita-artigos"):
                header_text = self._text_of_tag(header).strip()
                if not header_text:
                    continue

                header_cf = header_text.lower()
                if header_cf.startswith("citaç") or header_cf.startswith("citac"):
                    continue
                
                if self.debug_parsing:
                    logger.debug("Processing header: %r", header_text[:80])

                mode = "biblio"
                prev_inst_back = header.find_previous(
                    lambda tag: isinstance(tag, Tag)
                    and tag.name == "div"
                    and "inst_back" in (tag.get("class") or [])
                )
                if prev_inst_back:
                    inst_text = self._text_of_tag(prev_inst_back).lower()
                    if "produ" in inst_text and (
                        "técnica" in inst_text
                        or "tecnica" in inst_text
                        or re.search(r"t[^a-z0-9]{1,3}cnica", inst_text)
                    ):
                        mode = "tecnica"
                    elif "produ" in inst_text and (
                        "artística" in inst_text
                        or "artistica" in inst_text
                        or re.search(r"art[^a-z0-9]{1,3}stica", inst_text)
                        or "cultural" in inst_text
                    ):
                        mode = "artistica"

                content_nodes: List[Tag] = []
                for sibling in header.next_siblings:
                    if not isinstance(sibling, Tag):
                        continue
                    sibling_classes = sibling.get("class") or []
                    if "cita-artigos" in sibling_classes or "inst_back" in sibling_classes:
                        break
                    content_nodes.append(sibling)

                if not content_nodes:
                    continue

                frag_soup = BeautifulSoup(
                    "".join(str(n) for n in content_nodes), "html.parser"
                )
                
                if self.debug_parsing:
                    logger.debug("  mode=%s, content_nodes=%d", mode, len(content_nodes))

                if mode == "tecnica":
                    self._trace("producoes", "dispatch_header", strategy="tecnica", success=True, details=header_text[:80])
                    self._parse_producoes_tecnicas_items(
                        frag_soup,
                        tipo_secao=header_text,
                        header_text=header_text,
                    )
                elif mode == "artistica":
                    self._trace("producoes", "dispatch_header", strategy="artistica", success=True, details=header_text[:80])
                    self._parse_producoes_artisticas_items(
                        frag_soup,
                        tipo_secao=header_text,
                        header_text=header_text,
                    )
                else:
                    self._trace("producoes", "dispatch_header", strategy="biblio", success=True, details=header_text[:80])
                    self._parse_producoes_biblio_items(
                        frag_soup,
                        tipo_secao=header_text,
                        header_text=header_text,
                    )

        def _handle_projetos(_):
            self._parse_projetos_section()

        def _handle_orientacoes(section_root):
            self._parse_orientacoes_section(section_root)

        SECTION_HANDLERS = {
            "ProjetosPesquisa": _handle_projetos,
            "ProducoesCientificas": _handle_producoes,
            "Orientacoes": _handle_orientacoes,
        }

        # ------------------------------------------------------------------ #
        # Processamento das seções principais
        # ------------------------------------------------------------------ #
        for anchor in soup.find_all("a", attrs={"name": True}):
            name_attr = anchor.get("name", "")
            handler = SECTION_HANDLERS.get(name_attr)
            if not handler:
                continue

            self._trace("parse", "section_handler_found", strategy="anchor_name", success=True, details=name_attr)

            section_root = anchor.find_next(
                "div",
                class_="layout-cell layout-cell-12 data-cell",
            )
            self._trace(
                "parse",
                "section_root",
                strategy="find_next",
                success=bool(section_root),
                details=name_attr,
            )
            if not section_root:
                self._trace("parse", "section_handler_skipped", strategy="no_section_root", success=False, details=name_attr)
                continue

            try:
                handler(section_root)
                self._trace("parse", "section_handler_done", strategy="handler_call", success=True, details=name_attr)
            except Exception:
                if getattr(self, "debug_parsing", False):
                    logger.exception("handler for section %r failed", name_attr)
                self._trace("parse", "section_handler_done", strategy="handler_call", success=False, details=name_attr)

    # ------------------------------------------------------------------ #
    # Auxiliares de parsing de seções
    # ------------------------------------------------------------------ #

    def _text_of_tag(self, tag: Optional[Tag]) -> str:
        return tag.get_text(" ", strip=True) if tag else ""

    def _extract_lattes_id(self, href: Optional[str]) -> str:
        return _extract_lattes_id(href)

    def _extract_person_name(self, raw_text: str) -> str:
        """
        Extrai nome de pessoa do texto bruto, ignorando sufixos entre parenteses
        (ex.: "(Org.)") no momento da captura.
        """
        name = _normalize_whitespace((raw_text or "").strip())
        if not name:
            return ""

        name = name.strip(" ;,")

        # Remove apenas anotacoes parenteticas no fim quando o restante parece nome.
        m_suffix = re.search(r"\s*\(([^)]{1,80})\)\s*$", name)
        if m_suffix:
            base = re.sub(r"\s*\([^)]{1,80}\)\s*$", "", name).strip(" ;,")
            base_key = self._normalize_person_name(base)
            has_name_shape = bool(
                base_key
                and not re.search(r"\d", base)
                and not re.search(r"\bIn:\b", base, re.IGNORECASE)
                and len([w for w in re.split(r"\s+", base) if w]) >= 2
            )
            if has_name_shape:
                name = base

        return name

    def _extract_collaborators_from_biblio(
        self,
        span_transform: Tag,
        clean_text: str,
    ) -> List[Dict[str, Any]]:
        """
        Extrai colaboradores de um item bibliografico usando multiplas heuristicas:
        links, tags <b> sem classe, span data-tipo-ordenacao=autor e segmentação por ";"
        Retorna uma lista de dicionarios com 'nome' e 'id_lattes'
        """
        colaboradores: List[Dict[str, Any]] = []
        
        # Extrai colaboradores de links primeiro
        collab_from_links = self._extract_collaborators_from_tag(span_transform)
        for c in collab_from_links:
            parsed_name = self._extract_person_name(c.get("nome", ""))
            if re.search(r"\d+", parsed_name):
                continue
            if not parsed_name:
                continue
            colaboradores.append(
                {
                    "nome": parsed_name,
                    "id_lattes": c.get("id_lattes", ""),
                }
            )
        
        # Extrai de tags <b> sem classe
        for bold in span_transform.find_all("b", attrs={"class": None}):
            name = self._extract_person_name(self._text_of_tag(bold))
            if name and not any(c.get("nome") == name for c in colaboradores):
                colaboradores.append({"nome": name, "id_lattes": self.idLattes})
        
        # Extrai do span informacao-artigo data-tipo-ordenacao=autor
        span_autor = span_transform.find(
            "span",
            class_="informacao-artigo",
            attrs={"data-tipo-ordenacao": "autor"},
        )
        if span_autor:
            nome = self._extract_person_name(self._text_of_tag(span_autor))
            if nome and not any(c.get("nome") == nome for c in colaboradores):
                colaboradores.append(
                    {
                        "nome": nome,
                        "id_lattes": self._resolve_lattes_id_for_name(nome, colaboradores),
                    }
                )
        
        # Extrai colaboradores do texto limpo segmentando por ";"
        collab_text = re.split(r";\s+", clean_text)
        for possible_collab in collab_text:
            possible_collab = re.split(r"\s\.\s", possible_collab)[0]
            candidate = self._extract_person_name(possible_collab)

            if not candidate:
                continue
            
            if any(c.get("nome") == candidate for c in colaboradores):
                continue
            
            if re.search(r"\bIn:", candidate):
                break
            
            if re.search(r"\d+", candidate):
                continue
            
            if len(possible_collab.split()) <= 4:
                colaboradores.append(
                    {
                        "nome": candidate,
                        "id_lattes": self._resolve_lattes_id_for_name(candidate, colaboradores),
                    }
                )
        
        return colaboradores
    
    def _extract_collaborators_from_tag(self, tag: Tag) -> List[Dict[str, str]]:
        """
        Extrai colaboradores de um elemento HTML, procurando por links e outros padrões
        Retorna uma lista de dicionarios com 'nome' e 'id_lattes' (se disponivel)
        """
        collaborators: List[Dict[str, str]] = []

        if not tag:
            return collaborators

        for anchor in tag.find_all("a", href=True):
            href = anchor.get("href", "")
            lattes_id = self._extract_lattes_id(href)
            name = self._extract_person_name(self._text_of_tag(anchor))

            if lattes_id or name:
                collaborators.append({"nome": name, "id_lattes": lattes_id})

        return collaborators

    def _find_lattes_id_for(
        self,
        name: str,
        collaborators: List[Dict[str, Any]],
    ) -> str:
        """
        Encontra o id_lattes correspondente a um nome em uma lista de colaboradores
        Usa normalizacao consistente (minusculas, espacos normalizados, sem pontuacao)
        Compara com exatidao para evitar falsos positivos (e.g., 'Silva' vs 'da Silva')
        Retorna string vazia se nao encontrar
        """
        target = self._normalize_person_name(name)
        if not target:
            return ""
        for col in collaborators:
            col_name = self._normalize_person_name(col.get("nome", ""))
            if not col_name:
                continue
            if target == col_name:
                return col.get("id_lattes", "")
        return ""

    def _normalize_person_name(self, name: str) -> str:
        normalized = _normalize_whitespace((name or "").strip())
        return normalized.strip(" .;,").lower()

    def _owner_name_keys(self) -> set[str]:
        keys: set[str] = set()
        owner_name = self._normalize_person_name(getattr(self, "nomeCompleto", ""))
        if owner_name:
            keys.add(owner_name)
        for citation in getattr(self, "listaNomesCitacao", []) or []:
            citation_key = self._normalize_person_name(citation)
            if citation_key:
                keys.add(citation_key)
        return keys

    def _resolve_lattes_id_for_name(
        self,
        name: str,
        known_people: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """
        Resolve Lattes ID para um nome procurando na lista de pessoas conhecidas ou no proprietario
        
        Parametros:
        - name: nome da pessoa a resolver
        - known_people: lista de pessoas com IDs conhecidos/verificados (opcional).
                       Se None, apenas verifica se a pessoa e o proprietario (self).
        
        Retorna:
        - ID Lattes de 16 digitos se encontrado/resolvido
        - String vazia se nao houver corresponencia
        """
        candidate = (name or "").strip()
        if not candidate:
            return ""

        # Tenta encontrar em lista de pessoas conhecidas, se fornecida
        if known_people:
            known_id = self._find_lattes_id_for(candidate, known_people)
            if known_id:
                return known_id

        # Verifica se a pessoa e o proprietario (usando normalizacao consistente)
        if self._normalize_person_name(candidate) in self._owner_name_keys():
            return getattr(self, "idLattes", "")

        return ""

    def _fill_owner_id_in_people(self, people: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Preenche id_lattes para pessoas na lista se elas forem o proprietario
        
        Nota: Esta funcao nao procura por IDs em outros membros da lista,
        pois a lista tipicamente contem pessoas SEM IDs estabelecidos ainda.
        Apenas verifica se cada pessoa e o proprietario (self) pela comparacao de nome.
        
        Parametros:
        - people: lista de dicionarios com 'nome' e opcionalmente 'id_lattes'
        
        Modifica in-place e retorna a lista.
        """
        for person in people:
            if not isinstance(person, dict):
                continue
            if (person.get("id_lattes") or "").strip():
                continue
            person_name = person.get("nome", "")
            # Nao passa people como known_people pois contem IDs incompletos
            # Apenas verifica se a pessoa e o proprietario
            resolved_id = self._resolve_lattes_id_for_name(person_name, None)
            if resolved_id:
                person["id_lattes"] = resolved_id
        return people
    
    def _find_items_in_node(self, node: Tag) -> List[Tag]:
        """
        Encontra items em um no usando estruturas observadas no cache.
        """
        items = node.find_all("div", class_="artigo-completo", recursive=False)
        if not items:
            items = node.find_all(
                lambda tag: tag.parent == node
                and tag.name == "div"
                and tag.get("class")
                and any("layout-cell-11" in c for c in tag.get("class", []))
            )
        
        return items

    def _resolve_header_text(
        self,
        node: Tag,
        header_text: str = "",
    ) -> tuple[str, str]:
        """
        Resolve texto de cabeçalho e sua versão em lowercase para uma seção de produção.
        """
        resolved_header = header_text
        if not resolved_header:
            name_anchor = node.find("a", attrs={"name": True})
            if name_anchor:
                parent_b = name_anchor.find_parent("b")
                resolved_header = (
                    self._text_of_tag(parent_b) if parent_b else self._text_of_tag(node)
                )
            else:
                resolved_header = self._text_of_tag(node)

        return resolved_header, (resolved_header or "").lower()

    def _resolve_item_content(self, item: Tag) -> tuple[Tag, str]:
        """
        Resolve o nó principal de conteúdo de um item de produção e seu texto bruto.
        """
        content_cell = item.find("div", class_="layout-cell-11") or item
        span_transform = content_cell.find("span", class_="transform") or content_cell
        full_text = self._text_of_tag(span_transform)
        return span_transform, full_text
    
    def _process_bold_collaborators(
        self,
        item: Tag,
        colaboradores: List[Dict[str, Any]],
        update_existing: bool = False,
    ) -> None:
        """
        Processa tags <b> em um elemento HTML e atualiza/adiciona colaboradores
        Se update_existing=True, atualiza id_lattes para nomes existentes
        Se update_existing=False, adiciona apenas novos com correspondência case-insensitive
        Modifica a lista de colaboradores in-place
        """
        for bold in item.find_all("b"):
            bname = self._extract_person_name(self._text_of_tag(bold))
            if not bname:
                continue
            
            if update_existing:
                # Busca match exato e atualiza id_lattes se ainda não possui
                updated = False
                for col in colaboradores:
                    if col.get("nome") == bname:
                        if not col.get("id_lattes"):
                            col["id_lattes"] = self.idLattes
                        updated = True
                        break
                if not updated:
                    colaboradores.append({
                        "nome": bname,
                        "id_lattes": self.idLattes,
                    })
            else:
                # Match case-insensitive e adiciona novo se não existe
                found = False
                for col in colaboradores:
                    if col["nome"].upper() == bname.upper():
                        col["id_lattes"] = self.idLattes
                        found = True
                        break
                if not found:
                    colaboradores.append({
                        "nome": bname,
                        "id_lattes": self.idLattes,
                    })
    
    def _is_segment_author_list(
        self, 
        seg: str, 
        colaboradores: List[Dict[str, Any]]
    ) -> bool:
        """
        Função heuristica para determinar se um segmento de texto e provavelmente uma lista de autores
        Retorna True se o segmento parece ser uma lista de autores, False caso contrario
        """
        seg_check = (seg or "").strip()
        if not seg_check:
            return False
        
        # Remover anos no inicio do segmento
        seg_check = re.sub(r"^(19|20)\d{2}[\s\-:]+", "", seg_check)

        if ";" in seg_check or " et al" in seg_check.lower():
            return True
        
        # Verifica se o segmento tem muitos nomes separados por virgula
        if seg_check.count(",") >= 2:
            if re.search(
                r",\s+[A-Z](?:\.[A-Z\s.]*)?(?:\s*,|$)"      # inciais
                r"|,\s+[A-Z][A-Z]+(?:\s+[A-Z][A-Z]+)*\b"    # palavras todas em caps
                r"|,\s+[A-Z][a-z]+\b",                      # palavra capitalizada
                seg_check,
            ):
                return True
        
        # Checa por padrão de sobrenome em maiusculas (com opcionais particulas como 'DE', 'DA')
        if re.match(
            r"^[A-Z][A-Z]+(?:\s+(?:[A-Z][A-Z]+|D[AEIO]S?|DO|DA|DE|DOS|DAS))*\s*,\s*[A-Z](?:\.[A-Z]\.)?$",
            seg_check,
        ):
            return True

        # Checa por padrão de sobrenome capitalizado seguido de iniciais (e.g., "Zaniboni, C.L.")
        if re.match(
            r"^[A-ZÀ-ÖØ-Þ][a-zà-öø-ÿ]+(?:\s+[A-ZÀ-ÖØ-Þ][a-zà-öø-ÿ]+)*,\s*[A-Z](?:\.?[A-Z])?\.?$",
            seg_check,
        ):
            return True
        
        # Checa se o segmento corresponde a algum nome de colaborador (ignora maiusculas e pontos finais)
        seg_no_trailing = seg_check.rstrip(".").strip()
        seg_prefix = seg_no_trailing.split(".")[0].strip()
        seg_key = seg_no_trailing.lower()
        seg_prefix_key = seg_prefix.lower() if seg_prefix else ""
        for c in colaboradores:
            name = (c.get("nome", "") or "").strip()
            if not name:
                continue
            name_no_trailing = name.rstrip(".").strip()
            name_key = name_no_trailing.lower()
            if seg_key == name_key:
                return True
            name_prefix = name_no_trailing.split(".")[0].strip().lower()
            if seg_key == name_prefix:
                return True
            if seg_prefix_key and seg_prefix_key == name_prefix:
                return True
        
        return False

    def _should_skip_segment(self, seg: str) -> bool:
        """
        Determine se um segmento de texto deve ser ignorado durante a extração de titulo
        Retorna True se o segmento deve ser ignorado, False se pode ser um titulo
        """
        # Pula segmentos que começam com "v." ou "p." (marcadores de volume/pagina)
        if re.match(r"^[vp]\.\s*\d+", seg, re.IGNORECASE):
            return True

        # Pula segmentos que parecem com nomes de revista
        if re.search(r",\s*[vp]\s*$", seg, re.IGNORECASE):
            return True
        
        return False

    def _clean_title_from_segment(self, seg: str) -> str:
        """
        Limpa um segmento removendo "In:" e tudo que segue.
        Se o segmento começa com "In:", retorna string vazia (deve ser ignorado).
        Retorna o segmento limpo.
        """
        # Segmento que começa com "In:" é o venue/conferência — rejeita
        if re.match(r"^\s*In:\s*", seg, re.IGNORECASE):
            return ""
        if re.search(r"\bIn:", seg):
            seg = re.sub(r"([.a-zA-Zà-ÿ])[\s\-,;:]*In:.*$", r"\1", seg)
        return seg

    def _extract_title_from_citation_span(self, node: Optional[Tag]) -> str:
        """
        Extrai titulo de um span de citação (cvuri query param 'titulo').
        Retorna o titulo extraido ou string vazia se não encontrado.
        """
        if not node:
            return ""

        for span_citado in (
            node.find("span", class_="citacoes"),
            node.find("span", class_="citado"),
        ):
            if not span_citado:
                continue
            cvuri = span_citado.get("cvuri", "") or span_citado.get("href", "")
            if not cvuri:
                continue
            parsed = urlparse(cvuri)
            params = parse_qs(parsed.query)
            titulo_citado = params.get("titulo", [""])[0].strip()
            if not titulo_citado and "titulo=" in cvuri:
                m_titulo = re.search(r"[?&]titulo=([^&]+)", cvuri)
                if m_titulo:
                    titulo_citado = unquote(m_titulo.group(1)).strip()
            if titulo_citado:
                return titulo_citado.strip(" .,;:")

        return ""

    def _extract_title_from_segments(
        self,
        segments: List[str],
        colaboradores: List[Dict[str, Any]],
    ) -> str:
        """
        Extrai um titulo de uma lista de segmentos de texto
        Retorna o titulo extraido ou string vazia se não encontrado
        """
        titulo = ""
        
        if not segments:
            return titulo
        
        # Primeira tentativa: procura por um segmento que pareça ser um titulo
        short_candidate = ""
        for seg in segments:
            if len(seg) < 5:
                continue
            
            # Pula se for uma lista de autores
            if self._is_segment_author_list(seg, colaboradores):
                continue
            
            # Pula certos padrões
            if self._should_skip_segment(seg):
                continue
            
            # Limpa o segmento de "In:" (retorna "" se começar com "In:")
            seg = self._clean_title_from_segment(seg)
            
            # Pula segmentos que começam com "In:" ou ficaram vazios após limpeza
            seg_clean = seg.strip()
            if not seg_clean:
                continue

            # Pula segmentos sem letras suficientes (ex: apenas numeros)
            if len(re.findall(r"[A-Za-zÀ-ÿ]", seg_clean)) <= 1:
                continue

            # Preferencia por titulos longos
            words = len(seg_clean.split())
            if words >= 3:
                titulo = seg_clean
                break

            # Canditado curto para caso não encontremos um titulo mais longo
            if not short_candidate:
                short_candidate = seg_clean

        if not titulo and short_candidate:
            titulo = short_candidate
        
        # Fallback 1: tentar segundo segmento
        if not titulo and len(segments) >= 2:
            candidate = segments[1].strip()
            if len(candidate) > 15:
                words = len(candidate.split())
                if words >= 3 and not re.match(
                    r"^[vp]\.\s*\d+",
                    candidate,
                    re.IGNORECASE,
                ):
                    if not self._is_segment_author_list(candidate, colaboradores):
                        if not self._should_skip_segment(candidate):
                            cleaned = self._clean_title_from_segment(candidate)
                            if cleaned.strip() and len(re.findall(r"[A-Za-zÀ-ÿ]", cleaned)) > 1:
                                titulo = cleaned
        
        # Fallback 2: tentar primeiro segmento 
        if not titulo and len(segments) >= 1:
            candidate = segments[0].strip()
            if len(candidate) > 10 and not re.match(r"^[A-Z][a-z]*(?:\s+[A-Z][a-z]*)?(?:,\s*[A-Z]\.)?$", candidate):
                if "," not in candidate or len(candidate.split()) > 2:
                    if not self._is_segment_author_list(candidate, colaboradores):
                        if not self._should_skip_segment(candidate):
                            cleaned = self._clean_title_from_segment(candidate)
                            if cleaned.strip() and len(re.findall(r"[A-Za-zÀ-ÿ]", cleaned)) > 1:
                                titulo = cleaned
        
        return titulo

    def _clean_title_text(self, titulo: str) -> str:
        """
        Aplica limpeza final ao titulo extraido
        Remove marcadores de volume/pagina, anos, tags HTML e normaliza espaços
        Retorna o titulo limpo.
        """
        if not titulo:
            return ""

        # Regra global para "In:":
        # - se comeca com "In:", rejeita o titulo
        # - se aparece no meio, corta "In:" e tudo que segue
        titulo = titulo.strip()
        if re.match(r"^In:\s*", titulo, flags=re.IGNORECASE):
            return ""
        m_in = re.search(r"\bIn:\s*", titulo, flags=re.IGNORECASE)
        if m_in:
            titulo = titulo[: m_in.start()].rstrip(" ,.;:-")
        
        titulo = re.sub(
            r",?\s*v\.\s*\d+.*$",
            "",
            titulo,
            flags=re.IGNORECASE,
        )
        titulo = re.sub(
            r",?\s*p\.\s*\d+.*$",
            "",
            titulo,
            flags=re.IGNORECASE,
        )
        titulo = re.sub(r",?\s*\d{4}\s*$", "", titulo)
        titulo = re.sub(r"<[^>]+>", "", titulo)
        titulo = titulo.strip(" .,;:")

        # Titulo precisa conter mais de uma letra (evita aceitar apenas numeros/siglas ruins)
        if len(re.findall(r"[A-Za-zÀ-ÿ]", titulo)) <= 1:
            return ""
        
        return titulo

    def _clean_item_text(self, full_text: str, remove_numbered_prefix: bool = False) -> tuple[str, List[str]]:
        """
        Aplica limpeza padrão a texto de item de produção e extrai segmentos
        Remove links, tags HTML, citações e normaliza espaços
        Retorna tupla (texto_limpo, lista_de_segmentos)
        """
        if not full_text:
            return "", []
        
        clean_text = full_text
        if remove_numbered_prefix:
            clean_text = re.sub(r"\d{1,2}\.\s", "", clean_text)
        clean_text = re.sub(r"<a[^>]*>.*?</a>", "", clean_text)
        clean_text = re.sub(r"<sup>.*?</sup>", "", clean_text)
        clean_text = re.sub(r"<[^>]+>", "", clean_text)
        clean_text = re.sub(r"Citações:.*?(?:\d+\s*\|?\s*)*\d+", "", clean_text)
        
        segments = [
            seg.strip()
            for seg in re.split(r"\.\s+", clean_text)
            if seg.strip()
        ]
        
        return clean_text, segments

    def _extract_year_from_item(
        self,
        span_transform: Tag,
        full_text: str,
    ) -> str:
        """
        Extrai ano de um item de produção usando span data-tipo-ordenacao ou regex fallback
        Retorna string com ano de 4 digitos ou string vazia se não encontrado
        """
        ano = ""
        span_ano = span_transform.find(
            "span",
            class_="informacao-artigo",
            attrs={"data-tipo-ordenacao": "ano"},
        )
        if span_ano:
            ano = self._text_of_tag(span_ano).strip()
        if not ano:
            m_year = re.search(r"\b(20\d{2}|19\d{2})\b", full_text)
            if m_year:
                ano = m_year.group(1)
        return ano

    def _clean_collaborator_list(
        self,
        colaboradores: List[Dict[str, Any]],
        fallback_list: Optional[List[Dict[str, str]]] = None,
        check_word_count: bool = False,
    ) -> List[Dict[str, str]]:
        """
        Limpa e deduplica lista de colaboradores
        Remove nomes invalidos (anos, citações, muito longos)
        Normaliza formatação e remove duplicatas case-insensitive
        Retorna lista limpa de colaboradores
        """
        seen_names: set = set()
        cleaned_colaboradores: List[Dict[str, str]] = []

        def _initial_name_key(name: str) -> tuple[str, str]:
            """
            Gera chave (sobrenome, iniciais) para nomes no formato "SOBRENOME, A. B.".
            Retorna ("", "") para formatos fora desse padrao.
            """
            text = _normalize_whitespace((name or "").strip(" .;,"))
            if not text or "," not in text:
                return "", ""

            surname, initials_part = text.split(",", 1)
            surname_key = re.sub(r"\s+", " ", surname).strip().upper()
            initials = re.sub(r"[^A-Za-zÀ-ÿ]", "", initials_part).upper()

            if not surname_key or not re.fullmatch(r"[A-ZÀ-ÖØ-Þ]{1,8}", initials):
                return "", ""

            return surname_key, initials

        for col in colaboradores:
            raw_name = (col.get("nome") or "").strip()
            
            # Remove ". . " no final e conserta espaçamento
            raw_name = re.sub(r"\.\s*\.\s+.*$", ".", raw_name)
            raw_name = re.sub(r"\s+,", ",", raw_name)
            
            # Pula nomes invalidos
            if re.match(r"^\d{4}$", raw_name) or len(raw_name) < 3:
                continue
            if re.match(r"^Cita[çc][õo]es", raw_name, re.IGNORECASE):
                continue
            if raw_name.lower() in {
                "citações:",
                "citacoes:",
                "citado",
                "citações",
                "citacoes",
            }:
                continue
            
            # Normalize spacing
            display_name = _normalize_whitespace(raw_name)
            
            # Skip overly long names (likely not a person) - only if check enabled
            if check_word_count:
                word_count = len(display_name.split())
                if word_count > 12 or len(display_name) > 80:
                    continue
            
            # Adiciona o ponto pra nomes com uma letra
            if display_name and display_name[-1].isalpha():
                last_token = display_name.split()[-1]
                if len(last_token) == 1:
                    display_name = display_name + "."

            # Normaliza e deduplica
            normalized = re.sub(r"[.,;:]+$", "", display_name).strip()
            normalized = _normalize_whitespace(normalized)
            key = normalized.upper()

            # Deduplicacao conservadora para abreviacoes do mesmo autor:
            # "COSTA, N." ~= "COSTA, N. J." (mantem a versao mais especifica).
            cand_surname, cand_initials = _initial_name_key(display_name)
            merged_with_existing = False
            if cand_surname and cand_initials:
                for existing in cleaned_colaboradores:
                    ex_name = existing.get("nome", "")
                    ex_surname, ex_initials = _initial_name_key(ex_name)
                    if ex_surname != cand_surname or not ex_initials:
                        continue

                    if cand_initials.startswith(ex_initials) or ex_initials.startswith(cand_initials):
                        # Mantem o nome mais completo em iniciais e preserva id_lattes disponivel.
                        if len(cand_initials) > len(ex_initials):
                            existing["nome"] = display_name
                        if not existing.get("id_lattes") and col.get("id_lattes"):
                            existing["id_lattes"] = col.get("id_lattes", "")
                        merged_with_existing = True
                        break

            if merged_with_existing:
                continue

            if key not in seen_names:
                seen_names.add(key)
                cleaned_colaboradores.append(
                    {
                        "nome": display_name,
                        "id_lattes": col.get("id_lattes", ""),
                    }
                )
        
        # Usa lista de fallback se nada foi extraido
        if not cleaned_colaboradores and fallback_list:
            cleaned_colaboradores = fallback_list
        
        return cleaned_colaboradores

    # ------------------------------------------------------------------ #
    # Projetos de Pesquisa
    # ------------------------------------------------------------------ #

    def _extract_project_labeled_text(
        self,
        text: str,
        start_labels: List[str],
        all_labels: List[str],
    ) -> str:
        """
        Extrai o texto associado a um rotulo de projeto ate o proximo rotulo.
        Aceita multiplos rotulos iniciais equivalentes e retorna o primeiro
        valor nao vazio encontrado.
        """
        if not text:
            return ""

        for start_label in start_labels:
            start_index = text.find(start_label)
            if start_index == -1:
                continue

            content_start = start_index + len(start_label)
            end_positions = []
            for label in all_labels:
                if label in start_labels:
                    continue
                label_index = text.find(label, content_start)
                if label_index != -1:
                    end_positions.append(label_index)

            end_index = min(end_positions) if end_positions else len(text)
            value = text[content_start:end_index].strip(" .;")
            if value:
                return value

        return ""

    def _extract_project_funding_list(self, text: str) -> List[Dict[str, str]]:
        """
        Extrai uma lista de financiadores de um bloco de texto de projeto.
        Suporta um ou varios financiadores, tipicamente separados por "/".
        """
        labels_all = [
            "Descrição:",
            "Situação:",
            "Natureza:",
            "Alunos envolvidos:",
            "Integrantes:",
            "Financiador(es):",
            "Financiador:",
            "Financiador",
        ]
        funding_text = self._extract_project_labeled_text(
            text,
            ["Financiador(es):", "Financiador:", "Financiador"],
            labels_all,
        )
        if not funding_text:
            m_fin = re.search(
                rf"Financiador(?:\(es\))?\s*[:]?\s*(.+?)(?=(?:Descrição:|Situação:|Natureza:|Integrantes:|Coordenador(?:a)?:|{RE_NUMERO_PRODUCOES}|$))",
                text,
                re.DOTALL,
            )
            if m_fin:
                funding_text = m_fin.group(1).strip(" .;")

        if not funding_text:
            return []

        funding_text = _normalize_whitespace(funding_text).strip(" .;")
        funding_text = re.split(
            rf"Coordenador(?:a)?\s*:|{RE_NUMERO_PRODUCOES}",
            funding_text,
            maxsplit=1,
        )[0].strip(" .;")
        split_parts = re.split(r"\s+/\s+|\s*;\s*(?=[A-ZÀ-ÖØ-Þ(])", funding_text)

        funders: List[Dict[str, str]] = []
        seen_funders: set[tuple[str, str]] = set()
        for part in split_parts:
            cleaned = _normalize_whitespace(part).strip(" .;")
            if not cleaned:
                continue

            instituicao = ""
            tipo = ""

            m_sigla = re.match(r"^\(([^)]+)\)\s*(.*)$", cleaned)
            if m_sigla:
                instituicao = m_sigla.group(1).strip()
                remainder = m_sigla.group(2).strip()
                if " - " in remainder:
                    _, tipo_part = remainder.rsplit(" - ", 1)
                    tipo = tipo_part.strip(" .;")
                elif remainder and remainder.lower() in {"bolsa", "auxílio financeiro", "auxilio financeiro"}:
                    tipo = remainder
            else:
                if " - " in cleaned:
                    left, right = cleaned.rsplit(" - ", 1)
                    instituicao = left.strip(" .;")
                    tipo = right.strip(" .;")
                else:
                    instituicao = cleaned

            if not instituicao:
                continue

            key = (instituicao.lower(), tipo.lower())
            if key in seen_funders:
                continue
            seen_funders.add(key)
            funders.append(
                {
                    "instituicao": instituicao,
                    "tipo": tipo,
                }
            )

        return funders

    def _extract_project_year_range(self, year_text: str) -> tuple[str, str]:
        """
        Extrai ano inicial e final de um texto contendo informações de ano
        Retorna tupla (ano_inicio, ano_fim) ou ("", "") se não encontrado
        
        Suporta formatos:
        - Ano unico: "2020"
        - Intervalo: "2020 - 2023"
        - Aberto: "2020 - Atual" ou "2020 - Atualmente"
        """
        ano_inicio = ""
        ano_fim = ""
        
        if not year_text:
            return ano_inicio, ano_fim
        
        # Procura por intervalo completo (YYYY - YYYY)
        m_range = re.search(r"\b(\d{4})\b.*?-.*?\b(\d{4})\b", year_text)
        if m_range:
            return m_range.group(1), m_range.group(2)
        
        # Procura por intervalo aberto (YYYY - Atual/Atualmente)
        m_current = re.search(
            r"(\d{4})\s*-\s*(Atual|Atualmente)?",
            year_text,
            re.IGNORECASE,
        )
        if m_current:
            ano_inicio = m_current.group(1)
            return ano_inicio, ano_fim
        
        # Fallback: procura por ano unico
        m_single = re.search(r"(\d{4})", year_text)
        if m_single:
            ano_inicio = m_single.group(1)
        
        return ano_inicio, ano_fim

    def _extract_project_status(self, text: str) -> str:
        """
        Extrai status (situação) de um projeto a partir de texto
        Procura por padrões como "Em andamento", "Concluido"
        Retorna o status encontrado ou string vazia
        """
        if not text:
            return ""
        
        m_status = re.search(
            r"(?i)(Em andamento\.?|Em execução|Conclu[ií]do|Conclu[ií]da|Concluído|Concluída)",
            text,
        )
        if m_status:
            return m_status.group(1).strip().strip(" .;")
        
        return ""

    def _extract_project_members(self, membros_text: str) -> List[Dict[str, Any]]:
        """
        Extrai membros de um projeto a partir de texto contendo membros com papeis
        Suporta separadores: "/" e ";"
        Suporta formato: "Nome - Papel" ou "Nome-Papel" ou apenas "Nome"
        Retorna lista de dicionarios com 'nome', 'papel' e 'id_lattes'
        """
        membros: List[Dict[str, Any]] = []
        
        if not membros_text:
            return membros
        
        # Divide os membros por "/" e ";"
        parts = [
            part.strip()
            for part in re.split(r"\s*/\s*|;|\s+/\s+", membros_text)
            if part.strip()
        ]
        
        for part in parts:
            # Tenta extrair nome e papel separados por " - " ou "-"
            if " - " in part:
                name, role = [x.strip() for x in part.split(" - ", 1)]
            elif "-" in part:
                name, role = [x.strip() for x in part.split("-", 1)]
            else:
                name, role = part, ""

            if role:
                role = re.split(
                    r"(?i)\bN.{0,5}mero de produ.{0,5}es\b|Numero de producoes",
                    role,
                    maxsplit=1,
                )[0].strip(" .;")
            
            if name:
                member = {
                    "nome": name,
                    "papel": role,
                    "id_lattes": "",
                }
                # Associa ID Lattes se o membro e o proprio pesquisador
                member["id_lattes"] = self._resolve_lattes_id_for_name(name, membros)
                membros.append(member)
        
        return membros

    # ------------------------------------------------------------------ #
    # Áreas de atuação e Linhas de pesquisa
    # ------------------------------------------------------------------ #

    def _parse_area_atuacao_text(self, text: str) -> Dict[str, str]:
        """
        Converte uma string de area de atuação em objeto estruturado.

        Exemplo de entrada:
        'Grande área: X / Área: Y / Subárea: Z/Especialidade: W'
        """
        structured = {
            "grandeArea": "",
            "area": "",
            "subarea": "",
            "especialidade": "",
        }

        if not text:
            return structured

        patterns = [
            ("grandeArea", r"Grande\s*[áa]rea\s*:\s*([^/]+)"),
            ("area", r"(?:^|/)\s*[ÁA]rea\s*:\s*([^/]+)"),
            ("subarea", r"Sub[áa]rea\s*:\s*([^/]+)"),
            ("especialidade", r"Especialidade\s*:\s*([^/]+)"),
        ]

        for key, pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                structured[key] = _normalize_whitespace(match.group(1)).strip(" .;")

        return structured

    def _parse_areas_atuacao_section(self, section_root: Tag) -> None:
        """
        Extrai as áreas de atuação do pesquisador.
        Cada entrada é armazenada como objeto estruturado com chaves fixas:
        grandeArea, area, subarea, especialidade.
        """
        areas: List[Dict[str, str]] = []
        for cell in section_root.find_all("div", class_="layout-cell-9"):
            pad = cell.find("div", class_="layout-cell-pad-5")
            text = self._text_of_tag(pad if pad else cell)
            text = _normalize_whitespace(text).strip().strip(".")
            if text:
                areas.append(self._parse_area_atuacao_text(text))
        self.areasAtuacao = areas
        self._trace("areas_atuacao", "_parse_areas_atuacao_section", success=bool(areas), details=f"{len(areas)} areas")

    def _parse_linhas_pesquisa_section(self, section_root: Tag) -> None:
        """
        Extrai as linhas de pesquisa do pesquisador.
        Cada entrada é o nome da linha (sem o campo 'Objetivo').
        """
        linhas: List[str] = []
        for anchor in section_root.find_all("a", attrs={"name": re.compile(r"^LP_")}):
            name_cell = anchor.find_next_sibling("div", class_="layout-cell-9")
            if not name_cell:
                continue
            pad = name_cell.find("div", class_="layout-cell-pad-5")
            text = self._text_of_tag(pad if pad else name_cell)
            text = _normalize_whitespace(text).strip().strip(".")
            if text and not re.match(r"^objetivo", text, re.IGNORECASE):
                linhas.append(text)
        self.linhasPesquisa = linhas
        self._trace("linhas_pesquisa", "_parse_linhas_pesquisa_section", success=bool(linhas), details=f"{len(linhas)} linhas")

    # ------------------------------------------------------------------ #
    # Projetos de Pesquisa
    # ------------------------------------------------------------------ #

    def _parse_projetos_section(self) -> List[Dict[str, Any]]:
        soup = getattr(self, "_soup", None)
        if not soup:
            return []

        out: List[Dict[str, Any]] = []

        for container in soup.find_all("div", class_="layout-cell layout-cell-12 data-cell"):
            for anchor in container.find_all(attrs={"name": True}):
                try:
                    name_attr = anchor.get("name", "")
                    if "PP_" not in name_attr:
                        continue

                    self._trace("projetos", "anchor_detected", strategy="PP_*", success=True, details=name_attr)

                    # Title
                    title_div = anchor.find_next_sibling("div", class_="layout-cell layout-cell-9")
                    titulo = self._text_of_tag(title_div)
                    self._trace(
                        "projetos",
                        "extract_title_div",
                        strategy="find_next_sibling",
                        success=bool(titulo),
                        details=titulo[:80],
                    )

                    # Years
                    def _find_layout3_after(node: Tag) -> Optional[Tag]:
                        for sibling in node.find_next_siblings():
                            cls = sibling.get("class") or []
                            if sibling.name == "div" and any("layout-cell-3" in c for c in cls):
                                return sibling
                        return None

                    year_div = _find_layout3_after(anchor)

                    self._trace(
                        "projetos",
                        "extract_year_div",
                        strategy="layout3_after_anchor",
                        success=bool(year_div),
                    )

                    year_text = self._text_of_tag(year_div)
                    ano_inicio, ano_fim = self._extract_project_year_range(year_text)
                    self._trace(
                        "projetos",
                        "extract_year_range",
                        strategy="_extract_project_year_range",
                        success=bool(ano_inicio or ano_fim),
                        details=f"start={ano_inicio} end={ano_fim}",
                    )

                    # Description / metadata block
                    desc_block = None
                    if title_div is not None:
                        for sibling in title_div.find_next_siblings():
                            cls = sibling.get("class") or []
                            if sibling.name == "div" and any("layout-cell-9" in c for c in cls):
                                txt = self._text_of_tag(sibling)
                                if any(
                                    key in txt
                                    for key in (
                                        "Descrição",
                                        "Descri",
                                        "Situação",
                                        "Situa",
                                        "Integrantes",
                                        "Financiador",
                                    )
                                ):
                                    desc_block = sibling
                                    break

                    self._trace(
                        "projetos",
                        "extract_desc_block",
                        strategy="title_sibling_keyword_match" if desc_block else "none",
                        success=bool(desc_block),
                    )

                    descricao = ""
                    situacao = ""
                    natureza = ""
                    financiamento: List[Dict[str, str]] = []
                    membros: List[Dict[str, Any]] = []

                    if desc_block is not None:
                        raw = self._text_of_tag(desc_block)
                        raw = " ".join(raw.split())
                        low = raw
                        labels_main = [
                            "Descrição:",
                            "Situação:",
                            "Natureza:",
                            "Alunos envolvidos:",
                            "Integrantes:",
                            "Financiador(es):",
                            "Financiador:",
                            "Financiador",
                        ]

                        descricao = self._extract_project_labeled_text(
                            low,
                            ["Descrição:"],
                            labels_main,
                        )
                        self._trace("projetos", "descricao", strategy="slice_between_descricao", success=bool(descricao))
                        situacao = self._extract_project_labeled_text(
                            low,
                            ["Situação:"],
                            labels_main,
                        )
                        self._trace("projetos", "situacao", strategy="slice_between_situacao", success=bool(situacao))
                        natureza = self._extract_project_labeled_text(
                            low,
                            ["Natureza:"],
                            labels_main,
                        )
                        self._trace("projetos", "natureza", strategy="slice_between_natureza", success=bool(natureza))

                        financiamento = self._extract_project_funding_list(low)
                        self._trace("projetos", "financiamento", strategy="_extract_project_funding_list", success=bool(financiamento), details=f"count={len(financiamento)}")

                        membros_text = self._extract_project_labeled_text(
                            low,
                            ["Integrantes:"],
                            labels_main,
                        )
                        self._trace("projetos", "membros_text", strategy="slice_between_integrantes", success=bool(membros_text))
                        if not membros_text:
                            m_int = re.search(r"Integrantes\s*[:]?\s*(.+)", low)
                            if m_int:
                                membros_text = m_int.group(1).split("Financiador")[0].strip(" .;")
                            self._trace("projetos", "membros_text", strategy="regex_integrantes", fallback=True, success=bool(membros_text))

                        # Usa o metodo auxiliar para extrair membros
                        membros = self._extract_project_members(membros_text)
                        self._trace("projetos", "membros", strategy="_extract_project_members", success=bool(membros), details=f"count={len(membros)}")

                        # Fallbacks para descricao com labels
                        if not natureza:
                            m_nat = re.search(
                                r"(?is)natureza:\s*([^;\n<]+)",
                                low,
                            )
                            if m_nat:
                                natureza = m_nat.group(1).strip().strip(" .;")
                            self._trace("projetos", "natureza", strategy="regex_natureza", fallback=True, success=bool(natureza))

                        # Fallback geral para descrição
                        if not descricao and raw:
                            tmp = raw
                            indices = [
                                tmp.lower().find(label.lower())
                                for label in labels_main
                                if tmp.lower().find(label.lower()) != -1
                            ]
                            if indices:
                                descricao = tmp[: min(indices)].strip().strip(" .;")
                            else:
                                descricao = tmp.strip().strip(" .;")
                            self._trace("projetos", "descricao", strategy="generic_raw_slice", fallback=True, success=bool(descricao))

                        if not situacao and raw:
                            situacao = self._extract_project_status(raw)
                            self._trace("projetos", "situacao", strategy="_extract_project_status", fallback=True, success=bool(situacao))

                        # Limpeza final da descrição
                        cleaned_descricao = (descricao or "").strip().rstrip(".;")

                        proj = {
                            "titulo": titulo.strip()
                            if titulo
                            else name_attr.replace("PP_", "").strip(),
                            "anoInicio": ano_inicio,
                            "anoFim": ano_fim,
                            "descricao": cleaned_descricao,
                            "situacao": situacao,
                            "natureza": natureza,
                            "financiamento": financiamento,
                            "membros": membros,
                        }

                        self.listaPP.append(proj)
                        out.append(proj)
                        self._trace("projetos", "project_appended", strategy="append_output", success=True, details=proj.get("titulo", "")[:80])
                except Exception:
                    self._trace("projetos", "project_parse_error", strategy="exception", success=False)
                    continue

        return out

    # ------------------------------------------------------------------ #
    # Produções Bibliograficas
    # ------------------------------------------------------------------ #
    
    def _extract_doi_from_element(self, element: Optional[Tag]) -> str:
        """
        Extrai DOI de um elemento HTML procurando por link com classe 'icone-doi'
        Retorna a string DOI no formato "10.xxxx/xxxxx" ou string vazia se não encontrado
        """
        if not element:
            return ""
        
        doi_link = element.find("a", class_="icone-doi")
        if doi_link and doi_link.get("href"):
            doi_url = doi_link.get("href", "")
            m_doi = re.search(r"10\.\d+/\S+", doi_url)
            if m_doi:
                return m_doi.group(0)
        
        return ""

    def _parse_producoes_biblio_items(
        self,
        node: Tag,
        tipo_secao: Optional[str] = None,
        header_text: str = "",
    ) -> List[Dict[str, Any]]:
        try:
            if not node:
                return []

            out: List[Dict[str, Any]] = []

            header_text, header_cf = self._resolve_header_text(node, header_text)

            def infer_tipo_biblio(text: str) -> str:
                t = text.lower()
                is_chapter = bool(
                    "capít" in t
                    or "capit" in t
                    or re.search(r"cap(?:[ií]|[^a-z0-9]{1,3})t", t)
                )
                if "artigo" in t and (
                    "periód" in t 
                    or "periodic" in t 
                    or "completo" in t
                ):
                    return "Artigo em periódico"

                if "livro" in t and not is_chapter:
                    return "Livro publicado"
                
                if is_chapter:
                    return "Capítulo de livro"
                
                if "jornal" in t or "revista" in t:
                    return "Texto em jornal/revista"
                
                if "anais" in t or "congresso" in t or "evento" in t:
                    return "Trabalho em congresso"
                
                if "aceito" in t:
                    return "Artigo aceito"
                
                return "Produção bibliográfica"

            inferred_tipo = infer_tipo_biblio(tipo_secao or header_cf)
            if tipo_secao and inferred_tipo == "Produção bibliográfica":
                default_tipo = tipo_secao
            else:
                default_tipo = inferred_tipo
            self._trace("biblio", "infer_tipo", strategy="tipo_secao_or_infer_tipo_biblio", success=bool(default_tipo), details=default_tipo)
            
            if self.debug_parsing:
                logger.debug("_parse_producoes_biblio_items: header=%r tipo=%r", header_text[:60], default_tipo[:40] if default_tipo else "")

            items = self._find_items_in_node(node)
            self._trace("biblio", "find_items", strategy="_find_items_in_node", success=bool(items), details=f"count={len(items)}")
            
            if self.debug_parsing:
                logger.debug("  Found %d items to parse", len(items))

            for item in items:
                span_transform, full_text = self._resolve_item_content(item)
                if not full_text:
                    continue


                clean_text, segments = self._clean_item_text(full_text, remove_numbered_prefix=True)
                self._trace("biblio", "clean_item", strategy="_clean_item_text", success=True, details=f"segments={len(segments)}")
                
                ano = self._extract_year_from_item(span_transform, full_text)
                self._trace("biblio", "extract_year", strategy="_extract_year_from_item", success=bool(ano), details=ano)

                # Autores / colaboradores
                colaboradores = self._extract_collaborators_from_biblio(span_transform, clean_text)
                self._trace("biblio", "extract_collaborators", strategy="_extract_collaborators_from_biblio", success=bool(colaboradores), details=f"count={len(colaboradores)}")
                
                colaboradores = self._clean_collaborator_list(colaboradores)
                colaboradores = self._fill_owner_id_in_people(colaboradores)

                # Heuristicas para titulo
                titulo = ""

                titulo = self._extract_title_from_citation_span(span_transform)
                self._trace("biblio", "extract_title", strategy="citation_span", success=bool(titulo))

                if not titulo and segments:
                    titulo = self._extract_title_from_segments(segments, colaboradores)
                    self._trace("biblio", "extract_title", strategy="segments", fallback=True, success=bool(titulo))

                if titulo:
                    titulo = self._clean_title_text(titulo)
                    self._trace("biblio", "clean_title", strategy="_clean_title_text", success=bool(titulo))

                # Extração do DOI
                doi = self._extract_doi_from_element(span_transform)
                self._trace("biblio", "extract_doi", strategy="_extract_doi_from_element", success=bool(doi), details=doi)

                producao = {
                    "tipo": default_tipo,
                    "titulo": titulo,
                    "ano": ano,
                    "colaboradores": colaboradores,
                    "doi": doi,
                }
                
                if self.debug_parsing:
                    logger.debug("    Adding: %s (listaPB now has %d items)", titulo[:50] if titulo else "(no title)", len(self.listaPB) + 1)

                self.listaPB.append(producao)
                out.append(producao)

            return out
        except Exception as exc:
            if getattr(self, "debug_parsing", False):
                logger.error("Error in producoes_bibliograficas: %s", exc)
            self._trace("biblio", "parse_items", strategy="exception", success=False, details=str(exc))
            return []

    # ------------------------------------------------------------------ #
    # Produções tecnicas
    # ------------------------------------------------------------------ #
    
    def _fallback_title_from_full(
        self,
        full_text: str,
        ano_text: str,
        colaboradores: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """
        Heuristica de fallback para extrair titulo a partir do texto completo do item.
        Tenta multiplas heuristicas em ordem e retorna o primeiro candidato valido:
         - Candidato deve conter mais de uma letra
         - Candidato que comeca com "In:" e rejeitado; "In:" no meio e truncado
        Retorna o titulo extraido ou string vazia se nenhum candidato for valido.
        """
        if colaboradores is None:
            colaboradores = []

        candidates: List[str] = []

        # Candidato 1: texto apos ". . " (comum em finais de autores)
        m_double_period = re.search(r"\.\s+\.\s+", full_text)
        if m_double_period:
            candidates.append(full_text[m_double_period.end():])

        # Candidato 2: apos ponto seguido de palavra com letra minuscula (inicio do titulo)
        m_title_start = re.search(r"\.[\s;,]+(?=[a-z])", full_text)
        if m_title_start:
            candidates.append(full_text[m_title_start.end() - 1:])

        # Candidato 3: apos o ultimo ". " no terceiro final do texto
        idx = full_text.rfind(". ")
        if idx != -1 and idx > len(full_text) // 3:
            candidates.append(full_text[idx + 2:])

        # Candidato 4: texto inteiro, pulando o nome do primeiro colaborador se possivel
        anchor_pos = 0
        if colaboradores:
            first_name = colaboradores[0].get("nome", "")
            if first_name:
                idx2 = full_text.find(first_name)
                if idx2 != -1:
                    anchor_pos = idx2 + len(first_name)
        candidates.append(full_text[anchor_pos:])

        def _clean(s: str) -> str:
            s = re.sub(r"^[\s\.:;,-]+", "", s)
            s = re.sub(r"\s*\([^)]*\)\s*", " ", s)
            if ano_text:
                s = re.sub(
                    r"\.\s*" + re.escape(ano_text) + r"\s*(?:\.|$)",
                    "",
                    s,
                )
            s = s.rstrip(". ").strip()
            # Rejeita se comecar com "In:"
            if re.match(r"^\s*In:\s*", s, re.IGNORECASE):
                return ""
            # Trunca no "In:" do meio
            m_in = re.search(r"\bIn:\s*", s, re.IGNORECASE)
            if m_in:
                s = s[: m_in.start()].rstrip(" ,.;:-")
            return s

        for raw in candidates:
            snippet = _clean(raw)
            if snippet and len(re.findall(r"[A-Za-zÀ-ÿ]", snippet)) > 1:
                return snippet

        return ""

    def _parse_producoes_tecnicas_items(
        self,
        node: Tag,
        tipo_secao: Optional[str] = None,
        header_text: str = "",
    ) -> List[Dict[str, Any]]:
        try:
            if not node:
                return []

            out: List[Dict[str, Any]] = []

            header_text, header_cf = self._resolve_header_text(node, header_text)

            def infer_tipo_tecnica(text: str) -> str:
                t = text.lower()
                if "patente" in t:
                    return "Patente"
                if "marca" in t:
                    return "Marca"
                if "programa" in t and "computador" in t:
                    return "Software sem patente"
                if "software" in t and "patente" in t:
                    return "Software com patente"
                if "software" in t:
                    return "Software sem patente"
                if "produto" in t or "produto tecnolog" in t:
                    return "Produto tecnológico"
                if "processo" in t or "técnica" in t or "tecnica" in t:
                    return "Processo ou técnica"
                if "trabalho técnico" in t or "trabalho tecnico" in t:
                    return "Trabalho técnico"
                return "Outro tipo de produção técnica"

            default_tipo = tipo_secao or infer_tipo_tecnica(header_cf)
            self._trace("tecnica", "infer_tipo", strategy="tipo_secao_or_infer_tipo_tecnica", success=bool(default_tipo), details=default_tipo)

            items = self._find_items_in_node(node)
            self._trace("tecnica", "find_items", strategy="_find_items_in_node", success=bool(items), details=f"count={len(items)}")
            if getattr(self, "debug_parsing", False) and items == [node]:
                logger.debug(
                    "producoes_tecnicas: using node itself as item fallback "
                    "(id=%s, header=%r) node_html=%r",
                    getattr(self, "idLattes", ""),
                    header_cf[:120],
                    self._snippet(node),
                )

            for item in items:
                span_transform, full = self._resolve_item_content(item)
                if not full:
                    continue

                clean_text, _ = self._clean_item_text(full)

                # Ano
                ano = self._extract_year_from_item(span_transform, full)
                self._trace("tecnica", "extract_year", strategy="_extract_year_from_item", success=bool(ano), details=ano)

                # Autores
                colaboradores: List[Dict[str, Any]] = []
                collab_from_links = self._extract_collaborators_from_tag(span_transform)
                titulo = self._extract_title_from_citation_span(span_transform)
                is_patent = "patente" in default_tipo.lower() or "patente" in header_cf
                self._trace("tecnica", "extract_title", strategy="citation_span", success=bool(titulo))
                self._trace("tecnica", "patent_mode", strategy="is_patent_check", success=True, details=str(is_patent))

                try:
                    if is_patent:
                        if collab_from_links:
                            colaboradores.extend(collab_from_links)
                            self._trace("tecnica", "extract_collaborators", strategy="links", success=True, details=f"count={len(collab_from_links)}")

                        # Autores . Titulo ...
                        m_dot = re.search(r"\.\s+(?=[A-ZÀ-ÖØ-öø-ÿ])", full)
                        if m_dot:
                            self._trace("tecnica", "patent_authors_split", strategy="dot_separator", success=True)
                            authors_part = full[: m_dot.start()]
                            rest = full[m_dot.end() :]

                            parts = [
                                self._extract_person_name(part)
                                for part in re.split(r";", authors_part)
                                if part.strip()
                            ]
                            for part in parts:
                                if part and part not in [c.get("nome") for c in colaboradores]:
                                    colaboradores.append(
                                        {"nome": part, "id_lattes": ""}
                                    )

                            self._process_bold_collaborators(item, colaboradores, update_existing=False)

                            m_end = re.search(
                                r"(\d{4}|Patente:|Brasil|País)",
                                rest,
                                re.IGNORECASE,
                            )
                            if not titulo:
                                if m_end:
                                    titulo = rest[: m_end.start()].strip().rstrip(".,")
                                else:
                                    titulo = rest.split("\n")[0].strip().rstrip(".,")
                                self._trace("tecnica", "extract_title", strategy="patent_rest_slice", fallback=True, success=bool(titulo))
                        else:
                            self._trace("tecnica", "patent_authors_split", strategy="dot_separator", success=False)
                            if ano:
                                myear = re.search(
                                    r"\b" + re.escape(ano) + r"\b",
                                    full,
                                )
                                if myear:
                                    pos = myear.end()
                                    m_anchor = re.search(
                                        r"\.\s+(?=[A-ZÀ-ÖØ-öø-ÿ][a-zà-öø-ÿ])",
                                        full[pos:],
                                    )
                                    if m_anchor:
                                        self._trace("tecnica", "patent_anchor", strategy="year_anchor_dot", fallback=True, success=True)
                                        dot_rel = m_anchor.start()
                                        authors_blob = full[pos : pos + dot_rel]
                                        authors_blob = re.sub(
                                            r"^[\s\.:;-]+",
                                            "",
                                            authors_blob,
                                        ).strip()
                                        authors_blob = " ".join(
                                            authors_blob.split()
                                        )
                                        parts = [
                                            self._extract_person_name(p)
                                            for p in re.split(
                                                r";",
                                                authors_blob,
                                            )
                                            if p.strip()
                                        ]
                                        for part in parts:
                                            if part and not any(
                                                c.get("nome") == part
                                                for c in colaboradores
                                            ):
                                                colaboradores.append(
                                                    {
                                                        "nome": part,
                                                        "id_lattes": "",
                                                    }
                                                )

                                        self._process_bold_collaborators(item, colaboradores, update_existing=True)

                                        title_start = pos + m_anchor.end()
                                        rest = full[title_start:]
                                        m_title = re.search(
                                            r"^(.*?)(?:\.|$)",
                                            rest,
                                            re.DOTALL,
                                        )
                                        if not titulo and m_title:
                                            titulo = m_title.group(1).strip()
                                            self._trace("tecnica", "extract_title", strategy="patent_year_anchor_title", fallback=True, success=bool(titulo))
                                    else:
                                        self._trace("tecnica", "patent_anchor", strategy="year_anchor_dot", fallback=True, success=False)
                    else:
                        # Estrategia semelhante a producoes bibliograficas:
                        # combina links, <b>, span autor e fallback textual por ';'.
                        colaboradores = self._extract_collaborators_from_biblio(
                            span_transform,
                            clean_text,
                        )
                        self._trace(
                            "tecnica",
                            "extract_collaborators",
                            strategy="biblio_like",
                            success=bool(colaboradores),
                            details=f"count={len(colaboradores)}",
                        )

                        # Mantem compatibilidade com casos em que os autores estao
                        # apenas em trechos em negrito fora do span principal.
                        self._process_bold_collaborators(
                            item,
                            colaboradores,
                            update_existing=False,
                        )
                        self._trace(
                            "tecnica",
                            "extract_collaborators",
                            strategy="bold_merge",
                            fallback=True,
                            success=bool(colaboradores),
                            details=f"count={len(colaboradores)}",
                        )

                        # Fallback adicional para producoes tecnicas que expõem
                        # apenas o autor principal em data-tipo-ordenacao=autor.
                        if not colaboradores:
                            span_author = span_transform.find(
                                "span",
                                class_="informacao-artigo",
                                attrs={"data-tipo-ordenacao": "autor"},
                            )
                            if span_author:
                                auth_blob = self._extract_person_name(self._text_of_tag(span_author))
                                if auth_blob:
                                    colaboradores.append(
                                        {
                                            "nome": auth_blob,
                                            "id_lattes": self._resolve_lattes_id_for_name(auth_blob, collab_from_links),
                                        }
                                    )
                            self._trace(
                                "tecnica",
                                "extract_collaborators",
                                strategy="span_autor",
                                fallback=True,
                                success=bool(colaboradores),
                                details=f"count={len(colaboradores)}",
                            )

                        if not titulo:
                            titulo = self._fallback_title_from_full(
                                full,
                                ano,
                                colaboradores,
                            )
                            self._trace("tecnica", "extract_title", strategy="_fallback_title_from_full", fallback=True, success=bool(titulo))
                except Exception:
                    colaboradores = self._extract_collaborators_from_tag(item)
                    self._process_bold_collaborators(item, colaboradores, update_existing=False)
                    self._trace("tecnica", "item_parse", strategy="exception_fallback_collaborators", fallback=True, success=bool(colaboradores))

                # Rejeita titulo se for um ano
                if titulo and re.match(r"^\d{4}$", titulo.strip()):
                    titulo = ""

                # Aplica ultima limpeza
                if titulo:
                    titulo = self._clean_title_text(titulo)
                    self._trace("tecnica", "clean_title", strategy="_clean_title_text", success=bool(titulo))

                # Função de limpeza final dos colaboradores, removendo itens invalidos e duplicados
                colaboradores = self._clean_collaborator_list(colaboradores, collab_from_links, check_word_count=True)
                colaboradores = self._fill_owner_id_in_people(colaboradores)
                self._trace("tecnica", "clean_collaborators", strategy="_clean_collaborator_list+_fill_owner_id_in_people", success=True, details=f"count={len(colaboradores)}")

                producao_tecnica = {
                    "tipo": default_tipo,
                    "titulo": titulo,
                    "ano": ano,
                    "colaboradores": colaboradores,
                }

                self.listaPT.append(producao_tecnica)
                out.append(producao_tecnica)

            return out
        except Exception:
            self._trace("tecnica", "parse_items", strategy="exception", success=False)
            return []

    # ------------------------------------------------------------------ #
    # Produções artisticas
    # ------------------------------------------------------------------ #

    def _extract_authors_from_author_text(
        self,
        text: str,
        collab_from_links: List[Dict[str, str]],
        existing_colaboradores: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Extrai autores de um texto contendo lista de autores separados por ";"
        Remove tags HTML, valida nomes, e retorna lista de dicts com nome e id_lattes
        Evita duplicatas comparando com colaboradores ja extraidos
        """
        authors: List[Dict[str, Any]] = []
        if not text:
            return authors
        
        author_parts = re.split(r"\s*;\s*", text)
        for part in author_parts:
            # Remove tags HTML e caracteres de pontuação comuns
            clean = self._extract_person_name(re.sub(r"<[^>]+>", "", part))
            
            if (
                not clean
                or len(clean) <= 2
                or re.match(r"^[<>]|^\d+\.?$|^\d+$", clean)
                or self._is_artistic_media_descriptor(clean)
                or any(c["nome"] == clean for c in existing_colaboradores)
            ):
                continue
            
            lattes_id = self._find_lattes_id_for(clean, collab_from_links)
            authors.append({"nome": clean, "id_lattes": lattes_id})
        
        return authors

    def _is_artistic_media_descriptor(self, text: str) -> bool:
        """
        Detecta textos que descrevem o meio/formato da produção artística,
        e não nomes de colaboradores.
        """
        raw = (text or "").strip()
        if not raw:
            return False

        normalized = _normalize_whitespace(raw).strip(" .;:")
        lowered = normalized.lower()

        # Descrições entre parênteses são frequentes em "meio" (ex.: "(Exposição Fotográfica)").
        if re.fullmatch(r"\([^)]{2,120}\)", normalized):
            return True

        # Padrão frequente em itens artísticos: "2024 (Artístico Formativa)".
        if re.fullmatch(r"(?:19|20)\d{2}\s*\([^)]{2,120}\)", normalized):
            return True

        descriptor_tokens = (
            "exposi",
            "mostra",
            "festival",
            "instala",
            "instalac",
            "performance",
            "fotogr",
            "audiovisual",
            "video",
            "vídeo",
            "catálogo",
            "catalogo",
            "artist",
            "artíst",
            "formativ",
        )
        if any(token in lowered for token in descriptor_tokens):
            return True

        return False

    def _normalize_artistic_meio(self, text: str) -> str:
        """
        Normaliza o campo de meio removendo ruído de whitespace e pontuação.
        """
        if not text:
            return ""
        meio = _normalize_whitespace(text).strip()
        meio = re.sub(r"^(?:19|20)\d{2}\s*", "", meio).strip()
        while re.fullmatch(r"\([^()]{1,200}\)", meio):
            meio = meio[1:-1].strip()
        meio = meio.strip(".;:")
        return meio

    def _parse_producoes_artisticas_items(
        self,
        node: Tag,
        tipo_secao: Optional[str] = None,
        header_text: str = "",
    ) -> List[Dict[str, Any]]:
        try:
            if not node:
                return []

            out: List[Dict[str, Any]] = []

            header_text, header_cf = self._resolve_header_text(node, header_text)

            def infer_tipo_artistica(text: str) -> str:
                t = text.lower()
                if "artes" in t and (
                    "cênicas" in t 
                    or "nicas" in t 
                ):
                    return "Artes cênicas"
                
                if "artes" in t and "visuais" in t:
                    return "Artes visuais"
                
                if (
                    "desenho" in t
                    and "industrial" in t
                ):
                    return "Desenho industrial registrado"
                
                if "maquete" in t:
                    return "Maquete"
                
                if "partitura" in t and "musical" in t:
                    return "Partitura musical"
                
                if "música" in t or "musica" in t:
                    return "Música"
                
                if "outra" in t and (
                    "produção" in t 
                    or "producao" in t
                ):
                    return "Outra produção artística/cultural"
                
                return "Produção artística/cultural"

            default_tipo = tipo_secao or infer_tipo_artistica(header_cf)
            self._trace("artistica", "infer_tipo", strategy="tipo_secao_or_infer_tipo_artistica", success=bool(default_tipo), details=default_tipo)
            
            if self.debug_parsing:
                logger.debug("_parse_producoes_artisticas_items: header=%r tipo=%r", header_text[:60], default_tipo[:40] if default_tipo else "")

            items = self._find_items_in_node(node)
            self._trace("artistica", "find_items", strategy="_find_items_in_node", success=bool(items), details=f"count={len(items)}")
            
            if self.debug_parsing:
                logger.debug("  Found %d items to parse", len(items))

            for item in items:
                span_transform, full_text = self._resolve_item_content(item)
                if not full_text:
                    continue

                _, segments = self._clean_item_text(full_text)
                self._trace("artistica", "clean_item", strategy="_clean_item_text", success=True, details=f"segments={len(segments)}")
                
                collab_from_links = self._extract_collaborators_from_tag(span_transform)

                ano = self._extract_year_from_item(span_transform, full_text)
                self._trace("artistica", "extract_year", strategy="_extract_year_from_item", success=bool(ano), details=ano)

                # Autores / colaboradores
                colaboradores: List[Dict[str, Any]] = []

                # Preserva colaboradores com link quando presentes para evitar perda
                # de nomes/IDs em itens cujo texto de autores é incompleto.
                if collab_from_links:
                    colaboradores.extend(collab_from_links)
                    self._trace("artistica", "extract_collaborators", strategy="links", success=True, details=f"count={len(collab_from_links)}")

                # Extract first author from span data-tipo-ordenacao="autor"
                
                span_autor = span_transform.find(
                    "span",
                    class_="informacao-artigo",
                    attrs={"data-tipo-ordenacao": "autor"},
                )
                if span_autor:
                    first_author = self._extract_person_name(self._text_of_tag(span_autor))
                    if first_author:
                        lattes_id = self._find_lattes_id_for(first_author, collab_from_links)
                        colaboradores.append({"nome": first_author, "id_lattes": lattes_id})
                self._trace("artistica", "extract_collaborators", strategy="span_autor", success=bool(colaboradores), details=f"count={len(colaboradores)}")

                self._process_bold_collaborators(item, colaboradores, update_existing=True)
                self._trace("artistica", "extract_collaborators", strategy="bold_update_existing", fallback=True, success=bool(colaboradores), details=f"count={len(colaboradores)}")
                
                # Extrai autores de segmentos iniciais do texto
                if segments:
                    leading = segments[0].strip()
                    if self._is_segment_author_list(leading, colaboradores):
                        authors_from_leading = self._extract_authors_from_author_text(
                            leading, collab_from_links, colaboradores
                        )
                        colaboradores.extend(authors_from_leading)
                        self._trace("artistica", "extract_collaborators", strategy="leading_segment_author_list", fallback=True, success=bool(authors_from_leading), details=f"count={len(authors_from_leading)}")

                # Extrai mais autores de texto após o ano
                if ano:
                    year_match = None
                    for m in re.finditer(r"\b" + re.escape(ano) + r"\b", full_text):
                        year_match = m
                    if year_match:
                        after_year = full_text[year_match.end():].strip()
                        parts = re.split(r"\.\s+(?=[A-ZÀ-ÖØ-Þ])", after_year, maxsplit=1)
                        if parts:
                            authors_text = parts[0].strip()
                            authors_from_after = self._extract_authors_from_author_text(
                                authors_text, collab_from_links, colaboradores
                            )
                            colaboradores.extend(authors_from_after)
                            self._trace("artistica", "extract_collaborators", strategy="after_year_author_list", fallback=True, success=bool(authors_from_after), details=f"count={len(authors_from_after)}")

                # Heuristicas para titulo
                titulo = self._extract_title_from_citation_span(span_transform)
                self._trace("artistica", "extract_title", strategy="citation_span", success=bool(titulo))
                if not titulo:
                    titulo = self._extract_title_from_segments(segments, colaboradores)
                    self._trace("artistica", "extract_title", strategy="segments", fallback=True, success=bool(titulo))

                if titulo:
                    titulo = self._clean_title_text(titulo)
                    if len(titulo.split()) <= 3 and (
                        "," in titulo or titulo.isupper()
                    ):
                        titulo = ""

                if titulo:
                    # Truncar no ponto que precede informações de local/veículo
                    # que podem ser descrições em minusculas, siglas (SESC, UERJ) ou nomes de lugares
                    m_venue = re.search(r",\s+(?:[a-z]|[A-Z]{2,}|[A-Z][a-z]+\s+[A-Z])", titulo)
                    if m_venue:
                        titulo = titulo[:m_venue.start()].strip()

                    # Parar de coletar antes de "exposição"
                    m_exposicao = re.search(r"[\s,.(]+exposi[çc][ãa]o", titulo, re.IGNORECASE)
                    if m_exposicao:
                        titulo = titulo[:m_exposicao.start()].strip()
                        titulo = re.sub(r"[\s,.\-;:()]+$", "", titulo)
                
                # Meio: Extrair de segmentos apos o ano
                meio = ""
                if ano and segments:
                    try:
                        idx_year = max(
                            i for i, seg in enumerate(segments)
                            if re.search(r"\b" + re.escape(ano) + r"\b", seg)
                        )
                        for candidate in segments[idx_year + 1 :]:
                            candidate_clean = candidate.strip(" .;:")
                            candidate_clean = re.sub(r"^(?:19|20)\d{2}\s*", "", candidate_clean).strip(" -:;,.")
                            if candidate_clean:
                                meio = candidate_clean
                                break
                    except StopIteration:
                        pass
                    except ValueError:
                        pass

                if not meio and ano:
                    year_match = None
                    for m in re.finditer(r"\b" + re.escape(ano) + r"\b", full_text):
                        year_match = m
                    if year_match:
                        tail = full_text[year_match.end() :]
                        tail_parts = [
                            p.strip(" .;:")
                            for p in re.split(r"\.\s+", tail)
                            if p.strip(" .;:")
                        ]
                        for part in tail_parts:
                            normalized_part = re.sub(r"^(?:19|20)\d{2}\s*", "", part).strip(" -:;,.")
                            if normalized_part:
                                meio = normalized_part
                                break

                # Remove descritores de meio da lista de colaboradores e reaproveita como meio.
                descriptor_candidates: List[str] = []
                filtered_colaboradores: List[Dict[str, Any]] = []
                for colaborador in colaboradores:
                    cname = (colaborador.get("nome") or "").strip()
                    if self._is_artistic_media_descriptor(cname):
                        descriptor_candidates.append(cname)
                        continue
                    filtered_colaboradores.append(colaborador)
                colaboradores = filtered_colaboradores

                if not meio and descriptor_candidates:
                    meio = descriptor_candidates[0]

                meio = self._normalize_artistic_meio(meio)

                colaboradores = self._clean_collaborator_list(colaboradores, collab_from_links)
                colaboradores = self._fill_owner_id_in_people(colaboradores)
                self._trace("artistica", "clean_collaborators", strategy="_clean_collaborator_list+_fill_owner_id_in_people", success=True, details=f"count={len(colaboradores)}")

                producao = {
                    "tipo": default_tipo,
                    "titulo": titulo,
                    "ano": ano,
                    "colaboradores": colaboradores,
                    "meio": meio,
                }
                
                if self.debug_parsing:
                    logger.debug("    Adding: %s (listaPA now has %d items)", titulo[:50] if titulo else "(no title)", len(self.listaPA) + 1)

                self.listaPA.append(producao)
                out.append(producao)

            return out
        except Exception as exc:
            if getattr(self, "debug_parsing", False):
                logger.error("Error in producoes_artisticas: %s", exc)
            self._trace("artistica", "parse_items", strategy="exception", success=False, details=str(exc))
            return []

    # ------------------------------------------------------------------ #
    # Orientações (andamento / concluidas)
    # ------------------------------------------------------------------ #

    def _extract_tipo_orientacao(self, current_section: str, texto: str) -> str:
        """
        Extrai tipo de orientação (Mestrado, Doutorado, etc) baseado na seção atual e no texto
        Retorna o tipo_orientacao ou string vazia se não encontrado
        """
        sect = (current_section or "").lower()
        if (
            "dissertação" in sect
            or "disserta" in sect
            or "mestrad" in sect
        ):
            return "Mestrado"
        elif "tese" in sect or "doutor" in sect or "doutorad" in sect:
            return "Doutorado"
        elif "inicia" in sect or "iniciação" in sect:
            return "Iniciação Científica"
        elif "pós" in sect or "pos" in sect or "pós-doutorado" in sect:
            return "Pós-doutorado"

        # Fallback: procura no texto
        if re.search(r"\(Mestrado|Mestrado\)", texto, re.IGNORECASE):
            return "Mestrado"
        elif re.search(r"\(Doutorado|Doutoramento|Doutor\)", texto, re.IGNORECASE):
            return "Doutorado"
        elif re.search(r"Iniciação", texto, re.IGNORECASE):
            return "Iniciação Científica"

        return ""

    def _extract_tipo_projeto_orientacao(self, texto: str, current_section: str) -> str:
        """
        Extrai tipo de projeto (Tese, Dissertação, etc) baseado no texto e seção
        Retorna o tipo_projeto ou string vazia se não encontrado
        """
        try:
            mtype = re.search(r"\(([^)]{2,120})\)", texto)
            if mtype:
                tipo = self._normalize_tipo_projeto_orientacao(mtype.group(1).strip())
                if tipo:
                    return tipo
            return self._normalize_tipo_projeto_orientacao(current_section or "")
        except Exception:
            return self._normalize_tipo_projeto_orientacao(current_section or "")

    def _normalize_tipo_projeto_orientacao(self, raw: str) -> str:
        """
        Normaliza tipo de projeto para orientações
        Retorna tipo normalizado ou string vazia
        """
        if not raw:
            return ""
        text = raw.lower()
        if "tese" in text or "doutor" in text or "doutorado" in text:
            return "Tese"
        if "disser" in text or "mestrad" in text or "dissertação" in text:
            return "Dissertação"
        if "inicia" in text or "iniciação" in text:
            return "Iniciação Científica"
        return ""

    def _extract_orientadores_from_text(self, texto: str) -> List[Dict[str, Any]]:
        """
        Extrai orientadores e coorientadores de um texto
        Procura por labels como "Orientador:", "Coorientador:" seguidos de nomes
        Se não encontrar labels, retorna [self como orientador]
        Retorna lista de dicionarios com 'nome', 'papel' e 'id_lattes'/'idLattes'
        """
        orientadores: List[Dict[str, Any]] = []

        try:
            pattern_label = re.compile(
                r"(?P<label>Orientador(?:es)?|Coorientador(?:es)?|Co-orientador(?:es)?|Supervisor(?:es)?)\s*:\s*(?P<names>.+?)(?:\.|$)",
                re.IGNORECASE | re.DOTALL,
            )
            found = False
            for match in pattern_label.finditer(texto):
                found = True
                role = (match.group("label") or "").strip()
                names_blob = (match.group("names") or "").strip()
                parts = [
                    part.strip()
                    for part in re.split(
                        r"\s*(?:;|/|,|\band\b|\be\b|&| e )\s*",
                        names_blob,
                    )
                    if part.strip()
                ]
                for part in parts:
                    orientadores.append({
                        "nome": part,
                        "papel": role,
                        "id_lattes": self._resolve_lattes_id_for_name(part, orientadores),
                    })

                orientadores = self._fill_owner_id_in_people(orientadores)

            if not found:
                pattern_paren = re.compile(
                    r"\((?P<label>Orientador(?:es)?|Coorientador(?:es)?|Co-orientador(?:es)?|Supervisor(?:es)?)\)",
                    re.IGNORECASE,
                )
                mp = pattern_paren.search(texto)
                if mp:
                    role = (mp.group("label") or "").strip()
                    orientadores.append({
                        "nome": getattr(self, "nomeCompleto", ""),
                        "id_lattes": getattr(self, "idLattes", ""),
                        "papel": role,
                    })

        except Exception:
            orientadores = []

        # Se não encontrou orientadores, usa self como padrão
        if not orientadores:
            orientadores = [
                {
                    "nome": getattr(self, "nomeCompleto", ""),
                    "id_lattes": getattr(self, "idLattes", ""),
                    "papel": "Orientador",
                }
            ]

        return orientadores

    def _extract_nome_titulo_ano_orientacao(
        self,
        texto: str,
        is_concluida: bool = False,
    ) -> tuple[str, str, str]:
        """
        Extrai nome do orientando, titulo do projeto e ano de um texto de orientação
        Para andamento (is_concluida=False): procura por "Inicio: YYYY"
        Para concluida (is_concluida=True): procura por ano sem "Inicio:"
        Retorna tupla (nome, titulo, ano)
        """
        nome = ""
        titulo = ""
        ano = ""

        def _split_nome_titulo(pre_year_text: str) -> tuple[str, str]:
            """
            Divide "Nome. Titulo" sem quebrar iniciais no nome (ex.: "F.").
            """
            for mm in re.finditer(r"\.\s+", pre_year_text):
                left = pre_year_text[: mm.start()].strip()
                if not left:
                    continue

                token_match = re.search(r"([A-Za-zÀ-ÖØ-öø-ÿ]+)$", left)
                if token_match:
                    token = token_match.group(1)
                    # Não divide após iniciais simples como "F." em "Rodrigo F. Telles"
                    if len(token) == 1 and token.isupper():
                        continue

                right = pre_year_text[mm.end() :].strip()
                if right:
                    return left, right

            return "", ""

        if is_concluida:
            # Padrão para concluidas: Nome. Titulo. YYYY
            normalized_text = _normalize_whitespace(texto).strip()

            # Estratégia principal: usa o ano como âncora e divide Nome/Título
            # sem quebrar abreviações com ponto no nome.
            my = re.search(r"\b((?:19|20)\d{2})\b", normalized_text)
            if my:
                ano = my.group(1)
                pre = normalized_text[: my.start()].strip(" .;")
                if pre:
                    nome_split, titulo_split = _split_nome_titulo(pre)
                    if nome_split and titulo_split:
                        nome = nome_split
                        titulo = titulo_split

            if nome and titulo:
                nome = _normalize_whitespace(nome).strip(" .;")
                titulo = _normalize_whitespace(titulo).strip(" .;")
                ano = _normalize_whitespace(ano).strip(" .;")
                return nome, titulo, ano

            # Fallback: mantém regex guloso para casos ambíguos com iniciais.
            try:
                match = re.search(
                    r"^\s*(?P<nome>.+)\.\s+(?P<titulo>.*?)\.\s*(?P<ano>\d{4})\b",
                    normalized_text,
                    re.DOTALL | re.IGNORECASE,
                )
            except Exception:
                match = None

            if match:
                nome = (match.group("nome") or "").strip()
                titulo = (match.group("titulo") or "").strip()
                ano = (match.group("ano") or "").strip()
            else:
                # Fallback: procura por ano
                my = re.search(r"(?:In[ií]cio:\s*)?(19|20)\d{2}", texto)
                if my:
                    ano = my.group(0)
                    pre = texto[: my.start()]

                    last_dot = None
                    for mm in re.finditer(r"\.\s+", pre):
                        last_dot = mm
                    if last_dot:
                        nome = pre[: last_dot.start()].strip()
                        titulo = pre[last_dot.end() :].strip()
                    else:
                        parts = [
                            part.strip()
                            for part in pre.split(".")
                            if part.strip()
                        ]
                        nome = parts[0] if parts else ""
                        titulo = parts[1] if len(parts) > 1 else ""
                else:
                    parts = [
                        part.strip()
                        for part in re.split(r"\.\s+", texto)
                        if part.strip()
                    ]
                    if parts:
                        nome = parts[0]
                        titulo = parts[1] if len(parts) > 1 else ""
        else:
            # Padrão para andamento: Nome. Titulo. Inicio: YYYY
            try:
                match = re.search(
                    r"^\s*(?P<nome>.+)\.\s+(?P<titulo>.*?)\.\s*In[ií]cio:\s*(?P<ano>\d{4})",
                    texto,
                    re.DOTALL | re.IGNORECASE,
                )
            except Exception:
                match = None

            if match:
                nome = (match.group("nome") or "").strip()
                titulo = (match.group("titulo") or "").strip()
                ano = (match.group("ano") or "").strip()
            else:
                # Fallback: procura por "Inicio:"
                my = re.search(r"In[ií]cio:\s*(\d{4})", texto, re.IGNORECASE)
                if my:
                    ano = my.group(1)
                    pre = texto[: my.start()]
                else:
                    # Fallback: procura por qualquer ano
                    my2 = re.search(r"(?:^|\D)((?:19|20)\d{2})\b", texto)
                    if my2:
                        ano = my2.group(1)
                        pre = texto[: my2.start()]
                    else:
                        pre = texto

                last_dot = None
                for mm in re.finditer(r"\.\s+", pre):
                    last_dot = mm
                if last_dot:
                    nome = pre[: last_dot.start()].strip()
                    titulo = pre[last_dot.end() :].strip()
                else:
                    parts = [
                        part.strip()
                        for part in pre.split(".")
                        if part.strip()
                    ]
                    nome = parts[0] if parts else ""
                    titulo = parts[1] if len(parts) > 1 else ""

        nome = _normalize_whitespace(nome).strip(" .;")
        titulo = _normalize_whitespace(titulo).strip(" .;")
        ano = _normalize_whitespace(ano).strip(" .;")

        return nome, titulo, ano

    def _extract_area_from_orientacao(self, raw: str) -> str:
        """
        Extrai area de estudo de um texto de orientação
        Procura por padrões como "Mestrado em X", "Doutorado em X"
        Retorna a area extraida ou string vazia se não encontrada
        """
        if not raw:
            return ""
        txt = raw

        patterns = [
            r"\((?:[^)]{0,120}?)\b[Mm]estrad[oa]?\s+em\s+([^)]{1,120}?)\)",
            r"\((?:[^)]{0,120}?)\b[Dd]outor(?:ado)?\s+em\s+([^)]{1,120}?)\)",
            r"\((?:[^)]{0,120}?)\b[Gg]raduando\s+em\s+([^)]{1,120}?)\)",
            r"\((?:[^)]{0,120}?)\b[Ii]nicia(?:ç|c)ão\s+Cient[ií]fica.*?em\s+([^)]{1,120}?)\)",
            r"\((?:[^)]{0,200}?)\bem\s+([^)]{1,200}?)\)",
        ]

        for pattern in patterns:
            try:
                match = re.search(pattern, txt, re.IGNORECASE | re.DOTALL)
            except Exception:
                match = None
            if match:
                area = (match.group(1) or "").strip()
                area = re.sub(r"[\.,;]+$", "", area).strip()
                return area

        match2 = re.search(
            r"\b(?:Graduando|Mestrad[o|a]|Doutor(?:ado)?|Iniciação|Iniciacao)\s+em\s+([A-ZÁ-Úa-zá-ú0-9\s\-/&,.]{2,80})",
            txt,
            re.IGNORECASE,
        )
        if match2:
            return match2.group(1).strip()

        return ""

    def _extract_orientacao_funding_list(self, texto: str) -> List[Dict[str, str]]:
        """
        Extrai financiadores em itens de orientações.

        Em orientações, o financiador normalmente aparece no trecho final do item,
        muitas vezes sem rótulo explícito "Financiador:" (ex.: após " - ").
        """
        if not texto:
            return []

        normalized = _normalize_whitespace(texto).strip()
        if not normalized:
            return []

        # Remove bloco de orientador/coorientador para evitar ruído.
        normalized = re.split(
            r"\b(?:Orientador(?:es)?|Coorientador(?:es)?|Co-orientador(?:es)?|Supervisor(?:es)?)\b",
            normalized,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0].strip(" .;")

        candidate = normalized
        if " - " in normalized:
            candidate = normalized.split(" - ", 1)[1].strip()

        chunks = [
            _normalize_whitespace(chunk).strip(" .;")
            for chunk in re.split(r"\s*[;,]\s*", candidate)
            if chunk and chunk.strip(" .;")
        ]

        token_pattern = re.compile(
            r"\b(?:CNPq|CAPES|FAPESP|FINEP|PIBIC|PIBITI|PIBID|RHAE|"
            r"financiador(?:a|es)?|financiamento|fomento|bolsa|"
            r"Financiadora\s+de\s+Estudos\s+e\s+Projetos|"
            r"Fund(?:a|ação|acao)\w*\s+de\s+Amparo\s+[^,.;:]{0,120}|"
            r"Conselho\s+Nacional\s+de\s+Desenvolvimento\s+Cient[ií]fico\s+e\s+Tecnol[oó]gico|"
            r"Santander)\b",
            re.IGNORECASE,
        )

        acronym_map = {
            "cnpq": "CNPq",
            "capes": "CAPES",
            "fapesp": "FAPESP",
            "finep": "FINEP",
        }

        funders: List[Dict[str, str]] = []
        seen: set[tuple[str, str]] = set()

        def _add_funder(inst: str, tipo_val: str = "") -> None:
            instituicao_val = (inst or "").strip(" .;")
            tipo_clean = (tipo_val or "").strip(" .;")
            if not instituicao_val:
                return

            inst_key = instituicao_val.lower()
            if "finep" in inst_key or inst_key in {"financiadora de estudos e projetos"}:
                inst_key = "finep"
            elif inst_key in {"cnpq", "conselho nacional de desenvolvimento científico e tecnológico", "conselho nacional de desenvolvimento cientifico e tecnologico"}:
                inst_key = "cnpq"

            key_local = (inst_key, tipo_clean.lower())
            if key_local in seen:
                return
            seen.add(key_local)
            funders.append({"instituicao": instituicao_val, "tipo": tipo_clean})

        for raw_chunk in chunks:
            if not raw_chunk:
                continue
            if re.search(r"\bbolsa\s+de\s+valores\b", raw_chunk, re.IGNORECASE):
                continue
            if not token_pattern.search(raw_chunk):
                continue

            cleaned = raw_chunk
            cleaned = re.sub(r"\s*/\s*FINEP\b", "", cleaned, flags=re.IGNORECASE).strip(" .;")
            if len(cleaned) > 180:
                continue

            instituicao = cleaned
            tipo = ""

            m_agency_bolsa = re.match(
                r"^(?P<inst>[^/]{2,140})\s*/\s*(?P<tipo>Bolsa[^/]{1,140})$",
                cleaned,
                flags=re.IGNORECASE,
            )
            if m_agency_bolsa:
                instituicao = m_agency_bolsa.group("inst").strip(" .;")
                tipo = m_agency_bolsa.group("tipo").strip(" .;")
            elif " - " in cleaned:
                left, right = cleaned.rsplit(" - ", 1)
                if re.search(r"\bbolsa\b", right, re.IGNORECASE):
                    instituicao = left.strip(" .;")
                    tipo = right.strip(" .;")

            normalized_key = instituicao.lower().strip()
            if normalized_key in acronym_map:
                instituicao = acronym_map[normalized_key]

            _add_funder(instituicao, tipo)

        # Fallback conservador para marcadores explícitos fora do trecho final.
        for composite in re.finditer(r"\b(PIBIC|PIBITI)\s*/\s*(CNPq|CNPQ)\b", normalized, re.IGNORECASE):
            program = composite.group(1).upper()
            _add_funder("CNPq", program)

        for agency in re.finditer(r"\b(CNPq|CNPQ|CAPES|FAPESP|FINEP)\b", normalized, re.IGNORECASE):
            _add_funder(acronym_map.get(agency.group(1).lower(), agency.group(1).upper()))

        for bolsa_match in re.finditer(r"\bBolsa\b[^,.;()]{0,90}", normalized, re.IGNORECASE):
            bolsa_txt = _normalize_whitespace(bolsa_match.group(0)).strip(" .;")
            if not bolsa_txt:
                continue
            if re.search(r"\bbolsa\s+de\s+valores\b", bolsa_txt, re.IGNORECASE):
                continue
            _add_funder(bolsa_txt)

        return funders

    def _parse_orientacoes_section(self, orient_container) -> List[Dict[str, Any]]:
        if not getattr(self, "_soup", None) or not orient_container:
            return []

        out: List[Dict[str, Any]] = []

        current_section: str = ""
        status: Optional[str] = None  # None | "andamento" | "concluidas"

        for elem in orient_container.find_all(["div", "span"], recursive=True):
            if elem.name == "div" and (elem.get("class") or []):
                classes = elem.get("class") or []

                if "inst_back" in classes:
                    txt = self._text_of_tag(elem).lower()

                    if "andamento" in txt or "em andamento" in txt:
                        status = "andamento"
                        current_section = ""
                        self._trace("orientacoes", "status_switch", strategy="inst_back_andamento", success=True)
                        continue

                    if "conclu" in txt or "concluídas" in txt or "concluidas" in txt:
                        status = "concluidas"
                        current_section = ""
                        self._trace("orientacoes", "status_switch", strategy="inst_back_concluidas", success=True)
                        continue

                if "cita-artigos" in classes:
                    bold = elem.find("b")
                    if bold:
                        current_section = self._text_of_tag(bold).strip()
                        self._trace("orientacoes", "current_section", strategy="cita-artigos_bold", success=bool(current_section), details=current_section[:80])
                    continue

            if elem.name == "span" and "transform" in (elem.get("class") or []):
                if status not in ("andamento", "concluidas"):
                    continue

                txt = self._text_of_tag(elem)
                if not txt:
                    continue

                # ID do orientando
                id_orientando = ""
                try:
                    a_tag = elem.find("a", href=True)
                    if a_tag and a_tag.get("href"):
                        id_orientando = self._extract_lattes_id(a_tag["href"])
                except Exception:
                    id_orientando = ""

                # Extrai tipo de orientação
                tipo_orientacao = self._extract_tipo_orientacao(current_section, txt)
                self._trace("orientacoes", "tipo_orientacao", strategy="_extract_tipo_orientacao", success=bool(tipo_orientacao), details=tipo_orientacao)

                # Extrai tipo de projeto
                tipo_projeto = self._extract_tipo_projeto_orientacao(txt, current_section)
                self._trace("orientacoes", "tipo_projeto", strategy="_extract_tipo_projeto_orientacao", success=bool(tipo_projeto), details=tipo_projeto)

                # Extrai area
                area = self._extract_area_from_orientacao(txt)
                self._trace("orientacoes", "area", strategy="_extract_area_from_orientacao", success=bool(area), details=area)

                # Extrai financiamento (em orientações geralmente sem rótulo explícito)
                financiamento = self._extract_orientacao_funding_list(txt)
                for funding_item in financiamento:
                    if not (funding_item.get("tipo") or "").strip():
                        funding_item["tipo"] = "Bolsa"
                self._trace("orientacoes", "financiamento", strategy="_extract_orientacao_funding_list", success=bool(financiamento), details=f"count={len(financiamento)}")

                # ------------------------------------------------------------------
                # Em Andamento
                # ------------------------------------------------------------------
                if status == "andamento":
                    # Extrai nome, titulo, anno_inicio
                    nome, titulo, ano_inicio = self._extract_nome_titulo_ano_orientacao(
                        txt, is_concluida=False
                    )
                    self._trace("orientacoes", "extract_nome_titulo_ano", strategy="andamento_primary+fallbacks", success=bool(nome or titulo or ano_inicio), details=f"nome={bool(nome)} titulo={bool(titulo)} ano={ano_inicio}")

                    # Extrai orientadores
                    orientadores = self._extract_orientadores_from_text(txt)
                    self._trace("orientacoes", "orientadores", strategy="_extract_orientadores_from_text", success=bool(orientadores), details=f"count={len(orientadores)}")

                    orientando = {"nome": nome, "id_lattes": id_orientando}

                    entry_a = {
                        "orientando": orientando,
                        "titulo_projeto": titulo,
                        "ano_inicio": ano_inicio,
                        "tipo_projeto": tipo_projeto or current_section or "",
                        "tipo_orientacao": tipo_orientacao,
                        "area": area,
                        "orientadores": orientadores,
                        "financiamento": financiamento,
                    }

                    try:
                        self.listaOA.append(entry_a)
                        out.append(entry_a)
                        self._trace("orientacoes", "append_entry", strategy="andamento", success=True)
                    except Exception:
                        self._trace("orientacoes", "append_entry", strategy="andamento", success=False)
                        pass

                    continue

                # ------------------------------------------------------------------
                # Concluidas
                # ------------------------------------------------------------------
                if status == "concluidas":
                    # Extrai nome, titulo, ano
                    nome, titulo, ano = self._extract_nome_titulo_ano_orientacao(
                        txt, is_concluida=True
                    )
                    self._trace("orientacoes", "extract_nome_titulo_ano", strategy="concluidas_primary+fallbacks", success=bool(nome or titulo or ano), details=f"nome={bool(nome)} titulo={bool(titulo)} ano={ano}")

                    # Extrai orientadores
                    orientadores = self._extract_orientadores_from_text(txt)
                    self._trace("orientacoes", "orientadores", strategy="_extract_orientadores_from_text", success=bool(orientadores), details=f"count={len(orientadores)}")

                    orientando = {"nome": nome, "id_lattes": id_orientando}

                    entry_c = {
                        "orientando": orientando,
                        "titulo_projeto": titulo,
                        "ano": ano,
                        "tipo_projeto": tipo_projeto or current_section or "",
                        "tipo_orientacao": tipo_orientacao,
                        "area": area,
                        "orientadores": orientadores,
                        "financiamento": financiamento,
                    }

                    try:
                        self.listaOC.append(entry_c)
                        out.append(entry_c)
                        self._trace("orientacoes", "append_entry", strategy="concluidas", success=True)
                    except Exception:
                        self._trace("orientacoes", "append_entry", strategy="concluidas", success=False)
                        pass

        return out
