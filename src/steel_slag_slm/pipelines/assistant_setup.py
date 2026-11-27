"""Build everything the chat needs once (models, index, tools), shared by the CLI and the API."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from steel_slag_slm.agents.answer_citations import PaperCitations
from steel_slag_slm.agents.answer_writer import TextModel
from steel_slag_slm.agents.form_examples import ExamplePicker, load_examples
from steel_slag_slm.agents.question_form import FormColumn, QuestionForm
from steel_slag_slm.agents.router import RouteTools
from steel_slag_slm.config import (
    AnswerSettings,
    QuestionRoutingSettings,
    load_answer_settings,
    load_embedding_settings,
    load_gcp_settings,
    load_llm_gateway_settings,
    load_papers_table,
    load_question_routing_settings,
    load_similar_slag_settings,
    load_slag_catalogue_settings,
    load_slag_reference,
)
from steel_slag_slm.data.embedding_store import load_store
from steel_slag_slm.data.paper_registry import build_bigquery_client, read_citations
from steel_slag_slm.llm.gateway import GatewayClient, JsonModel, connect
from steel_slag_slm.llm.prompts import PromptTemplate, load_prompt
from steel_slag_slm.pipelines.drive_folders import connect_to_parsed_folder, index_folder_id
from steel_slag_slm.pipelines.prepare_evidence import (
    PreparedEvidence,
    build_route_tools,
    load_form_columns,
    prepare_evidence,
)
from steel_slag_slm.rag.embeddings import Embedder
from steel_slag_slm.rag.retrieval import CrossEncoderScorer, TwoStageRetriever
from steel_slag_slm.rag.similar_slags import SlagProfile
from steel_slag_slm.schemas import PaperCitation

# Builds the client for one gateway model, with thinking on or off
ModelConnector = Callable[[str, bool], GatewayClient]


@dataclass(frozen=True, slots=True)
class ModelChoice:
    """The models one chat message uses, by gateway name, as the user picked them."""

    reader_model: str
    answer_model: str
    thinking: bool = False  # only models with a thinking_request can switch it


@dataclass(frozen=True, slots=True)
class Assistant:
    """The parts that answer a chat message, built once and reused for every message."""

    reader: JsonModel  # reads a message into a form
    read_prompt: PromptTemplate
    columns: list[FormColumn]
    tools: RouteTools
    routing: QuestionRoutingSettings
    examples: ExamplePicker | None
    writer: TextModel  # writes the answer from the evidence
    answer_prompt: PromptTemplate
    answer_settings: AnswerSettings
    paper_citations: PaperCitations  # title, authors, year and DOI of papers, by OpenAlex id
    connect_model: ModelConnector

    def with_models(self, choice: ModelChoice) -> "Assistant":
        """This assistant reading and writing with the chosen models; the rest is shared."""
        return replace(
            self,
            reader=self.connect_model(choice.reader_model, choice.thinking),
            writer=self.connect_model(choice.answer_model, choice.thinking),
        )

    def prepare(
        self,
        message: str,
        profile: SlagProfile | None = None,
        previous: QuestionForm | None = None,
    ) -> PreparedEvidence:
        """Read the message, run the tools it needs and package their results as evidence."""
        return prepare_evidence(
            message,
            self.reader,
            self.read_prompt,
            self.columns,
            self.tools,
            self.routing,
            profile,
            previous,
            self.examples,
        )


def build_assistant(
    parsed_folder: str | None = None,
    reader_model: str | None = None,
    answer_model: str | None = None,
) -> Assistant:
    """Connect to Drive, BigQuery and the gateway and load the search index (slow: once).

    Model names left as None use llm_gateway.reader_model / answer_model in base.yaml.
    """
    routing = load_question_routing_settings()
    embedding = load_embedding_settings()
    gateway = load_llm_gateway_settings()
    answer_settings = load_answer_settings()

    drive_service, _ = connect_to_parsed_folder(parsed_folder)
    encoder = Embedder(embedding.model)
    store = load_store(
        drive_service,
        index_folder_id(drive_service, parsed_folder),
        embedding.model,
        encoder.dimension,
    )
    retriever = TwoStageRetriever(
        store,
        encoder,
        CrossEncoderScorer(embedding.reranker),
        embedding.query_prefix,
        embedding.top_papers,
        embedding.candidates,
    )
    gcp = load_gcp_settings()
    client = build_bigquery_client(gcp.project, gcp.credentials_file)
    catalogue_settings = load_slag_catalogue_settings()
    # Queries run and bill in gcp.project; the tables may sit in the team's data project
    data_project = gcp.data_project or client.project
    tools = build_route_tools(
        client,
        catalogue_settings,
        load_similar_slag_settings(),
        load_slag_reference(),
        lambda query, top_k: retriever.search(query, top_k),
        data_project,
    )
    papers_table = load_papers_table()
    sdk = connect(gateway)  # one connection pool for every model the chat switches to

    def connect_model(model: str, thinking: bool) -> GatewayClient:
        return GatewayClient(gateway, model, sdk, thinking)

    def paper_citations(paper_ids: Sequence[str]) -> dict[str, PaperCitation]:
        return read_citations(client, papers_table, paper_ids) if paper_ids else {}

    return Assistant(
        reader=connect_model(reader_model or gateway.reader_model, False),
        read_prompt=load_prompt(routing.prompt_name),
        columns=load_form_columns(
            client, f"{data_project}.{catalogue_settings.dataset}", routing.max_listed_values
        ),
        tools=tools,
        routing=routing,
        examples=ExamplePicker(load_examples(routing.examples_name), routing.examples_k, encoder),
        writer=connect_model(answer_model or gateway.answer_model, False),
        answer_prompt=load_prompt(answer_settings.prompt_name),
        answer_settings=answer_settings,
        paper_citations=paper_citations,
        connect_model=connect_model,
    )
