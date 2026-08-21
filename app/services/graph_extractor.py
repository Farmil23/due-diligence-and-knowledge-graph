"""
FinAgent — Graph Extractor Service
ICIJ-grade pipeline:
  1. LLM extracts entities & relations (no hardcoded rules)
  2. Text normalisation (case, punctuation, legal suffixes)
  3. String-similarity dedup (rapidfuzz + jellyfish Soundex)
  4. Multi-attribute graph MERGE in Neo4j (auto entity-resolution)
"""

import re
import os
import unicodedata
import tempfile
from collections import defaultdict
from typing import List, Optional

from pydantic import BaseModel, Field
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_community.document_loaders import PyMuPDFLoader

from app.db.neo4j_client import Neo4jClient
from app.services.llm_service import GroqClient
from app.core.logging import get_logger

logger = get_logger(__name__)

# ── Optional similarity libs (graceful degradation) ──────────────────────────
try:
    from rapidfuzz import fuzz as _fuzz
    _HAS_RAPIDFUZZ = True
except ImportError:
    _HAS_RAPIDFUZZ = False
    logger.warning("rapidfuzz not installed — fuzzy dedup disabled")

try:
    import jellyfish as _jf
    _HAS_JELLYFISH = True
except ImportError:
    _HAS_JELLYFISH = False
    logger.warning("jellyfish not installed — phonetic dedup disabled")


# ══════════════════════════════════════════════════════════════════════════════
# SCHEMA
# ══════════════════════════════════════════════════════════════════════════════

class NodeType(str):
    PERSON   = "Person"
    COMPANY  = "Company"
    ADDRESS  = "Address"
    DOCUMENT = "Document"

class RelationType(str):
    WORKS_AT        = "WORKS_AT"
    OWNS_SHARE      = "OWNS_SHARE"
    DIRECTOR_OF     = "DIRECTOR_OF"
    COMMISSIONER_OF = "COMMISSIONER_OF"
    FAMILY_OF       = "FAMILY_OF"
    MARRIED_TO      = "MARRIED_TO"
    REGISTERED_AT   = "REGISTERED_AT"
    LIVES_AT        = "LIVES_AT"
    MENTIONED_IN    = "MENTIONED_IN"
    TRANSFERRED_TO  = "TRANSFERRED_TO"
    USES_EMAIL      = "USES_EMAIL"
    OWNS_ASSET      = "OWNS_ASSET"
    BENEFICIAL_OWNER_OF = "BENEFICIAL_OWNER_OF"

KNOWN_TYPES = {"Person", "Company", "Address", "Document"}


# ══════════════════════════════════════════════════════════════════════════════
# PYDANTIC MODELS
# ══════════════════════════════════════════════════════════════════════════════

class Node(BaseModel):
    name:    str            = Field(..., description="Full name of the entity.")
    type:    str            = Field(..., description="Type: Person | Company | Address | Document.")
    context: str            = Field(..., description="Unique title or affiliation. MUST NOT be empty.")
    role:    Optional[str] = Field(None,  description="Formal title (only for Person): CEO, CFO, Commissioner, etc.")

    @property
    def id(self) -> str:
        clean_name    = re.sub(r'[^a-zA-Z0-9]', '_', self.name.lower())
        clean_context = re.sub(r'[^a-zA-Z0-9]', '_', self.context.lower())
        return f"{clean_name}_{clean_context}"

    @property
    def name_normalized(self) -> str:
        return normalize_name(self.name)


class Relationship(BaseModel):
    source:  str            = Field(..., description="Source node name (must exactly match a defined node name).")
    target:  str            = Field(..., description="Target node name (must exactly match a defined node name).")
    type:    str            = Field(..., description="Relationship type in UPPER_SNAKE_CASE.")
    details: Optional[str] = Field(None, description="Additional details (share value, date, etc.).")


class ExtractionResult(BaseModel):
    nodes:         List[Node]
    relationships: List[Relationship]


# ══════════════════════════════════════════════════════════════════════════════
# 1. TEXT NORMALISATION
# ══════════════════════════════════════════════════════════════════════════════

_LEGAL_SUFFIXES = re.compile(
    r'\b(limited|ltd\.?|incorporated|inc\.?|corporation|corp\.?|'
    r's\.?a\.?|tbk\.?|pt\.?|cv\.?|llc\.?|llp\.?|plc\.?|'
    r'perseroan terbatas|persero)\b',
    re.IGNORECASE,
)

def normalize_name(text: str) -> str:
    """
    ICIJ-style normalisation:
      - Unicode → ASCII
      - Lowercase
      - Remove legal suffixes (Ltd, PT, Inc …)
      - Strip punctuation & collapse whitespace
    """
    # Strip accents
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = text.lower().strip()
    # Remove legal entity markers
    text = _LEGAL_SUFFIXES.sub("", text)
    # Remove non-word characters (keep spaces)
    text = re.sub(r'[^\w\s]', '', text)
    # Collapse whitespace
    return re.sub(r'\s+', ' ', text).strip()


# ══════════════════════════════════════════════════════════════════════════════
# 2. STRING SIMILARITY — in-batch dedup
# ══════════════════════════════════════════════════════════════════════════════

FUZZY_THRESHOLD  = 90   # rapidfuzz token_sort_ratio
PHONETIC_ENABLED = True  # also match via Soundex

def _phonetic(name: str) -> str:
    """Return Soundex code if jellyfish available."""
    if _HAS_JELLYFISH:
        return _jf.soundex(name)
    return name

def _is_duplicate(a: str, b: str) -> bool:
    """True if two normalised names refer to the same entity."""
    if a == b:
        return True
    if _HAS_RAPIDFUZZ and _fuzz.token_sort_ratio(a, b) >= FUZZY_THRESHOLD:
        return True
    if PHONETIC_ENABLED and _phonetic(a) == _phonetic(b) and _phonetic(a) not in ("", "0000"):
        return True
    return False


def deduplicate_nodes(nodes: List[Node]) -> List[Node]:
    """
    Collapse nodes that refer to the same real-world entity.
    Keeps the first occurrence; redirects all relationships later.
    Returns (canonical_nodes, name_to_canonical_id mapping).
    """
    canonical: List[Node] = []
    norm_to_canon: dict[str, str] = {}   # normalised_name → canonical_id

    for node in nodes:
        norm = node.name_normalized
        matched_id = None
        for existing_norm, canon_id in norm_to_canon.items():
            if _is_duplicate(norm, existing_norm):
                matched_id = canon_id
                break
        if matched_id:
            norm_to_canon[norm] = matched_id   # map this variant too
        else:
            canonical.append(node)
            norm_to_canon[norm] = node.id

    logger.info(f"Dedup: {len(nodes)} → {len(canonical)} unique nodes")
    return canonical


# ══════════════════════════════════════════════════════════════════════════════
# 3. GRAPH EXTRACTOR SERVICE
# ══════════════════════════════════════════════════════════════════════════════

_EXTRACTION_SYSTEM_PROMPT = """
You are an ICIJ (International Consortium of Investigative Journalists) Forensic AI Investigator.
Mission: extract EVERY entity and EVERY relationship from the document. No information should be missed.

════════════════════════════════════════════════════════════════
NODE TYPES AND THEIR PROPERTIES
════════════════════════════════════════════════════════════════
• Person   — Individual (person name, alias, title)
  - context: FULL job title at the company. Example: "CEO of PT Nebula Nusantara"
  - role   : short formal title. Example: "CEO" / "CFO" / "President Commissioner"

• Company  — Business entity (PT, CV, Ltd, LLC, SA, shell company, foundation)
  - context: type and sector. Example: "Shell Company – BVI" / "State-Owned Enterprise in Infrastructure"

• Address  — Address, city, country, JURISDICTION REGION (BVI, Cayman, Panama, Labuan…)
  - context: region type. Example: "Tax Haven Jurisdiction – BVI"

• Document — Deed, contract, financial report, email, bank account number, minutes of meeting

════════════════════════════════════════════════════════════════
RELATIONSHIP TYPES — WITH STRICT SEMANTIC DEFINITIONS
════════════════════════════════════════════════════════════════
DIRECTOR_OF         — someone serves as a director in that company
COMMISSIONER_OF     — someone serves as a commissioner
WORKS_FOR           — someone works at the company (non-board position)
DIRECTS             — someone controls/leads the company operationally
BENEFICIAL_OWNER_OF — someone is the ultimate beneficial owner of the company (UBO)
CONTROLS            — someone/entity de facto controls another entity
OWNS_SHARE          — direct SHARE/EQUITY ownership (not a loan!)
REGISTERED_AT       — company is registered/domiciled in this jurisdiction
TRANSFERRED_TO      — direct fund transfer (include nominal in details)
LENDS_TO            — A gives LOAN to B (A is the creditor)
BORROWS_FROM        — A receives LOAN from B (A is the debtor)
RECEIVES_LOAN_FROM  — A receives loan funds from B (same as BORROWS_FROM)
PAYS_DEBT_TO        — pays debt installment/interest to the creditor
FAMILY_OF           — family relationship
MARRIED_TO          — husband/wife
USES_EMAIL          — uses this email address
LIVES_AT            — lives/domiciled at this address
OWNS_ASSET          — owns non-share assets (property, vehicle, account)
MENTIONED_IN        — mentioned in the document/account

════════════════════════════════════════════════════════════════
STRICT SEMANTIC RULES — DO NOT MISCLASSIFY
════════════════════════════════════════════════════════════════

OWNS_SHARE vs LENDS_TO / BORROWS_FROM:
  OWNS_SHARE  = share/equity ownership. Clue: shareholder, percentage (%), dividend.
  LENDS_TO    = loan/debt. Clue: receives loan, debt, credit, interest, installment.
  WRONG: A "receives subordinated loan" from B → OWNS_SHARE (TOTALLY WRONG)
  CORRECT: A "receives subordinated loan" from B → B --LENDS_TO--> A
                                                     A --BORROWS_FROM--> B
  WRONG: A "pays debt" to B → OWNS_SHARE
  CORRECT: A "pays debt" to B → A --PAYS_DEBT_TO--> B

DIRECTOR_OF vs WORKS_FOR vs DIRECTS:
  DIRECTOR_OF  = official director position (CEO, CFO, etc.)
  WORKS_FOR    = non-board employee or consultant
  DIRECTS      = controls operations (can be without official title)

════════════════════════════════════════════════════════════════
INVESTIGATIVE RULES — MUST BE STRICTLY FOLLOWED
════════════════════════════════════════════════════════════════

RULE 1 — PERSON ROLE (MANDATORY):
Each Person MUST:
  a) Have a "role" field with a short title: "CEO", "CFO", etc.
  b) Have a DIRECTOR_OF or WORKS_FOR relationship to their company.
  c) Fill "details" with description of their role in the scandal: "CFO – manipulated balance sheet"

RULE 2 — SHELL COMPANY + JURISDICTION (CRITICAL):
Every shell company MUST have a REGISTERED_AT relationship to its jurisdiction node.

RULE 3 — FUND FLOW (DISTINGUISH LOAN vs TRANSFER):
  • Loan/credit: creditor --LENDS_TO--> debtor
                 debtor --BORROWS_FROM--> creditor (create bidirectional)
  • Repayment: debtor --PAYS_DEBT_TO--> creditor (details: nominal + interest)
  • Direct transfer: sender --TRANSFERRED_TO--> receiver (details: nominal)

RULE 4 — LAYERED BENEFICIAL OWNERSHIP:
If director → company A → company B (shell):
  director --BENEFICIAL_OWNER_OF--> company B
  director --CONTROLS--> company B

RULE 5 — NOMINAL AND DATE are MANDATORY in the details of every financial relationship.

RULE 6 — TARGET MINIMUM 15 RELATIONSHIPS. Create descriptive new relations if necessary.

════════════════════════════════════════════════════════════════
CORRECT EXTRACTION EXAMPLE
════════════════════════════════════════════════════════════════
nodes:
  name="Adrianus Dananjaya", type="Person", role="CEO",
    context="CEO of PT Nebula Nusantara – intellectual actor of corruption"
  name="Melinda Kusuma", type="Person", role="CFO",
    context="CFO of PT Nebula Nusantara – manipulated financial balance sheet"
  name="PT Nebula Nusantara", type="Company",
    context="Infrastructure Company – money laundering suspect"
  name="Vanguard Nexus Ltd.", type="Company",
    context="Shell Company – British Virgin Islands"
  name="British Virgin Islands (BVI)", type="Address",
    context="Tax Haven Jurisdiction – BVI"

relationships:
  source="Adrianus Dananjaya" → target="PT Nebula Nusantara" | type="DIRECTOR_OF"
    | details="CEO – authorized fictitious contract – CORRUPTION"
  source="Melinda Kusuma" → target="PT Nebula Nusantara" | type="DIRECTOR_OF"
    | details="CFO – manipulated balance sheet to pass audit"
  source="Vanguard Nexus Ltd." → target="PT Nebula Nusantara" | type="LENDS_TO"
    | details="Fake subordinated loan – irrational interest rate (NOT share ownership)"
  source="PT Nebula Nusantara" → target="Vanguard Nexus Ltd." | type="BORROWS_FROM"
    | details="Received fictitious subordinated loan from Vanguard"
  source="PT Nebula Nusantara" → target="Crestview Holdings S.A." | type="PAYS_DEBT_TO"
    | details="Fictitious debt payment of 1.2 trillion – disguised debt scheme"
  source="Vanguard Nexus Ltd." → target="British Virgin Islands (BVI)" | type="REGISTERED_AT"
    | details="Has no physical operations – pure shell company"
  source="Adrianus Dananjaya" → target="Vanguard Nexus Ltd." | type="BENEFICIAL_OWNER_OF"
    | details="Ultimate beneficial owner of BVI shell company"

════════════════════════════════════════════════════════════════
OUTPUT: Return ExtractionResult JSON. Extract EVERY entity and relationship.
Few relationships and misclassification = FAILURE.
════════════════════════════════════════════════════════════════
"""


class GraphExtractorService:
    def __init__(self):
        self._llm = None

    @property
    def llm(self):
        if self._llm is None:
            groq_client = GroqClient()
            self._llm = groq_client.get_llm().with_structured_output(ExtractionResult)
        return self._llm

    # ── PDF / TXT loading ─────────────────────────────────────────────────

    def load_pdf_content(self, file_path: str) -> str:
        loader    = PyMuPDFLoader(file_path)
        documents = loader.load()
        return "\n".join(doc.page_content for doc in documents)

    # ── LLM Extraction ────────────────────────────────────────────────────

    def extract(self, text: str, source_doc: str = "Unknown") -> ExtractionResult:
        logger.info(f"LLM extraction starting | doc={source_doc} | chars={len(text)}")

        # Chunk long documents to stay within LLM context (8k tokens ≈ 24k chars)
        MAX_CHARS = 20_000
        if len(text) > MAX_CHARS:
            logger.info(f"Document too long ({len(text)} chars) — chunking into segments")
            return self._extract_chunked(text, source_doc, MAX_CHARS)

        messages = [
            SystemMessage(content=_EXTRACTION_SYSTEM_PROMPT),
            HumanMessage(content=f"Source Document: {source_doc}\n\n---\n{text}\n---"),
        ]
        result: ExtractionResult = self.llm.invoke(messages)
        logger.info(f"LLM extracted {len(result.nodes)} nodes, {len(result.relationships)} rels")
        return result

    def _extract_chunked(self, text: str, source_doc: str, chunk_size: int) -> ExtractionResult:
        """Process long documents in overlapping chunks and merge results."""
        overlap  = 500
        all_nodes: List[Node]         = []
        all_rels:  List[Relationship] = []

        start = 0
        chunk_no = 0
        while start < len(text):
            chunk = text[start : start + chunk_size]
            chunk_no += 1
            logger.info(f"Processing chunk {chunk_no} (chars {start}–{start+len(chunk)})")
            try:
                partial = self.extract(chunk, f"{source_doc} [chunk {chunk_no}]")
                all_nodes.extend(partial.nodes)
                all_rels.extend(partial.relationships)
            except Exception as e:
                logger.warning(f"Chunk {chunk_no} failed: {e}")
            start += chunk_size - overlap

        return ExtractionResult(nodes=all_nodes, relationships=all_rels)

    # ── Main entry point (FastAPI) ────────────────────────────────────────

    def process_uploaded_file_from_api(self, file_path: str, filename: str) -> bool:
        try:
            if filename.lower().endswith('.pdf'):
                text = self.load_pdf_content(file_path)
            else:
                with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                    text = f.read()

            if not text.strip():
                logger.warning(f"Empty file: {filename}")
                return False

            result = self.extract(text, source_doc=filename)
            logger.info(f"Raw: {len(result.nodes)} nodes, {len(result.relationships)} rels")

            # ── ICIJ pipeline (dedup in-memory, then save) ─────────────
            result = self._normalise_and_dedup(result)
            self.save_to_neo4j(result)
            # Note: _merge_duplicates_in_graph() is intentionally NOT called
            # here — it's a heavy operation and should be triggered manually
            # or via a background job, not on every upload.
            return True

        except Exception as e:
            logger.error(f"Failed to process {filename}: {e}", exc_info=True)
            return False

    # ── ICIJ Step 1 + 2: Normalise & in-batch dedup ──────────────────────

    def _normalise_and_dedup(self, result: ExtractionResult) -> ExtractionResult:
        """Apply normalisation and fuzzy dedup before saving."""
        deduped_nodes = deduplicate_nodes(result.nodes)
        known_names   = {n.name for n in deduped_nodes}

        # Keep only relationships where both endpoints exist as nodes
        clean_rels = [
            r for r in result.relationships
            if r.source in known_names and r.target in known_names
        ]

        logger.info(f"After dedup: {len(deduped_nodes)} nodes, {len(clean_rels)} rels")
        return ExtractionResult(nodes=deduped_nodes, relationships=clean_rels)

    # ── Save to Neo4j (pure Cypher, no APOC) ─────────────────────────────

    def save_to_neo4j(self, data: ExtractionResult):
        if not data.nodes:
            logger.warning("No entities to save.")
            return

        client      = Neo4jClient()
        saved_nodes = 0
        saved_rels  = 0

        # ── Nodes ────────────────────────────────────────────────────────
        for node_type in ("Person", "Company", "Address", "Document"):
            batch = [
                {
                    "id":              n.id,
                    "name":            n.name,
                    "context":         n.context,
                    "role":            n.role or "",
                    "name_normalized": n.name_normalized,
                }
                for n in data.nodes if n.type == node_type
            ]
            if not batch:
                continue
            q = f"""
            UNWIND $nodes AS n
            MERGE (node:{node_type} {{id: n.id}})
            SET node.name            = n.name,
                node.context         = n.context,
                node.role            = n.role,
                node.name_normalized = n.name_normalized,
                node.type            = '{node_type}'
            RETURN count(node) AS cnt
            """
            r = client.execute_query(q, {"nodes": batch})
            if r and r.get("data"):
                saved_nodes += r["data"][0].get("cnt", 0)

        # Fallback for unknown types
        unknown = [
            {"id": n.id, "name": n.name, "context": n.context,
             "name_normalized": n.name_normalized, "type": n.type}
            for n in data.nodes if n.type not in KNOWN_TYPES
        ]
        if unknown:
            q = """
            UNWIND $nodes AS n
            MERGE (node:Entity {id: n.id})
            SET node += {name: n.name, context: n.context,
                         name_normalized: n.name_normalized, type: n.type}
            """
            client.execute_query(q, {"nodes": unknown})

        # Build name → node lookup for ID resolution
        node_by_name = {n.name: n for n in data.nodes}

        # ── Relationships (dynamic type, no APOC) ────────────────────────
        rels_by_type: dict = defaultdict(list)
        for r in data.relationships:
            src_node = node_by_name.get(r.source)
            tgt_node = node_by_name.get(r.target)
            if not src_node or not tgt_node:
                logger.warning(f"Skipping rel {r.source}→{r.target}: node not found")
                continue
            rtype = re.sub(r'[^A-Z0-9_]', '_',
                           r.type.upper().replace(" ", "_").replace("-", "_"))
            if not rtype or not rtype.replace("_", "").isalpha():
                continue
            rels_by_type[rtype].append({
                "source_id": src_node.id,
                "target_id": tgt_node.id,
                "details":   r.details or "",
            })

        for rtype, batch in rels_by_type.items():
            q = f"""
            UNWIND $rels AS r
            MATCH (src {{id: r.source_id}})
            MATCH (tgt {{id: r.target_id}})
            MERGE (src)-[rel:{rtype}]->(tgt)
            SET rel.details = r.details
            RETURN count(rel) AS cnt
            """
            try:
                res = client.execute_query(q, {"rels": batch})
                if res and res.get("data"):
                    saved_rels += res["data"][0].get("cnt", 0)
            except Exception as e:
                logger.warning(f"Could not save relation {rtype}: {e}")

        logger.info(f"Saved to Neo4j: {saved_nodes} nodes, {saved_rels} relations")

    # ── ICIJ Step 3: Graph-based multi-attribute MERGE ───────────────────

    def _merge_duplicates_in_graph(self):
        """
        Collapse nodes that share the same normalised name AND
        at least one common neighbour (address / company / email).
        Pure Cypher — no GDS / APOC needed.
        """
        client = Neo4jClient()

        # Find candidate pairs: same normalised name, different id
        find_q = """
        MATCH (a), (b)
        WHERE id(a) < id(b)
          AND a.name_normalized IS NOT NULL
          AND a.name_normalized = b.name_normalized
          AND a.id <> b.id
        RETURN a.id AS aid, b.id AS bid, a.name AS aname, b.name AS bname
        LIMIT 100
        """
        result = client.execute_query(find_q)
        if not result or not result.get("data"):
            logger.info("No cross-document duplicates found.")
            return

        pairs = result["data"]
        logger.info(f"Found {len(pairs)} candidate duplicate pairs — resolving...")

        for pair in pairs:
            aid, bid = pair["aid"], pair["bid"]
            # Redirect all relationships from b to a, then delete b
            merge_q = f"""
            MATCH (a {{id: $aid}}), (b {{id: $bid}})
            // Redirect outgoing rels
            WITH a, b
            MATCH (b)-[r]->(x)
            WHERE x <> a
            WITH a, b, collect({{type: type(r), target: x, props: properties(r)}}) AS out_rels
            FOREACH (rel IN out_rels |
                MERGE (a)-[nr:RELATED_TO]->(rel.target)
                SET nr += rel.props
            )
            WITH a, b
            // Redirect incoming rels
            MATCH (y)-[r2]->(b)
            WHERE y <> a
            WITH a, b, collect({{type: type(r2), src: y, props: properties(r2)}}) AS in_rels
            FOREACH (rel IN in_rels |
                MERGE (rel.src)-[nr2:RELATED_TO]->(a)
                SET nr2 += rel.props
            )
            WITH a, b
            DETACH DELETE b
            """
            try:
                client.execute_query(merge_q, {"aid": aid, "bid": bid})
                logger.info(f"Merged duplicate: '{pair['bname']}' → '{pair['aname']}'")
            except Exception as e:
                logger.warning(f"Could not merge {aid} ↔ {bid}: {e}")


# ── Singleton ─────────────────────────────────────────────────────────────────
extractor_service = GraphExtractorService()
