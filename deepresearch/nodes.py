from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.prompts import SystemMessagePromptTemplate, HumanMessagePromptTemplate, ChatPromptTemplate, MessagesPlaceholder
import operator

from langgraph.types import Command, Send

from deepresearch.prompts import *
from deepresearch.client_init import init_llm
from deepresearch.chunk_prep import create_chunks
from deepresearch.qdrant_setup import rag_pipeline_setup, retrieve_from_store
from deepresearch.schema import AgentState, ResearchState, Sections, Literal, Queries, SearchResult, Feedback

from configuration import LLM_CONFIG

llm = init_llm(
    provider=LLM_CONFIG["provider"],
    model=LLM_CONFIG["model"],
    temperature=LLM_CONFIG["temperature"]
)

def resource_setup_node(state: AgentState, config: RunnableConfig):
    thread_id = config.get("configurable").get("thread_id")
    directory_path = state.get("resource_path")
    chunks = create_chunks(directory_path)
    rag_pipeline_setup(thread_id, chunks)


def report_structure_planner_node(state: AgentState, config: RunnableConfig):
    report_structure_planner_system_prompt = ChatPromptTemplate.from_messages([
        SystemMessagePromptTemplate.from_template(REPORT_STRUCTURE_PLANNER_SYSTEM_PROMPT_TEMPLATE),
        HumanMessagePromptTemplate.from_template(
            template="""
            Topic: {topic}
            Outline: {outline}
            """
        ),
        MessagesPlaceholder(variable_name="messages")
    ])

    report_structure_planner_llm = report_structure_planner_system_prompt | llm
    result = report_structure_planner_llm.invoke(state)
    return {"messages": [result]}


def human_feedback_node(state: AgentState, config: RunnableConfig)->Command[Literal["section_formatter", "report_structure_planner"]]:
    human_message = input("Please provide feedback on the report structure (type 'continue' to continue): ")
    report_structure = state.get("messages")[-1].content
    if human_message == "continue":
        return Command(
            goto="section_formatter",
            update={"messages": [HumanMessage(content=human_message)], "report_structure": report_structure}
        )
    else:
        return Command(
            goto="report_structure_planner",
            update={"messages": [HumanMessage(content=human_message)]}
        )



def section_formatter_node(state: AgentState, config: RunnableConfig) -> Command[Literal["research_agent"]]:
    section_formatter_system_prompt = ChatPromptTemplate.from_messages([
        SystemMessagePromptTemplate.from_template(SECTION_FORMATTER_SYSTEM_PROMPT_TEMPLATE),
        HumanMessagePromptTemplate.from_template(template="{report_structure}"),
    ])

    section_formatter_llm = section_formatter_system_prompt | llm.with_structured_output(Sections)
    result = section_formatter_llm.invoke(state)
    return Command(
        update={"sections": result.sections},
        goto=[
            Send(
                "research_agent",
                {
                    "section": s,
                }
            ) for s in result.sections
        ]
    )


def section_knowledge_node(state: ResearchState, config: RunnableConfig):
    section_knowledge_system_prompt = ChatPromptTemplate.from_messages([
        SystemMessagePromptTemplate.from_template(SECTION_KNOWLEDGE_SYSTEM_PROMPT_TEMPLATE),
        HumanMessagePromptTemplate.from_template(template="{section}"),
    ])

    section_knowledge_llm = section_knowledge_system_prompt | llm
    result = section_knowledge_llm.invoke(state)
    return {"knowledge": result.content}


def query_generator_node(state: ResearchState, config: RunnableConfig):
    query_generator_system_prompt = ChatPromptTemplate.from_messages([
        SystemMessagePromptTemplate.from_template(QUERY_GENERATOR_SYSTEM_PROMPT_TEMPLATE),
        HumanMessagePromptTemplate.from_template(template="Section: {section}\nPrevious Queries: {searched_queries}\nReflection Feedback: {reflection_feedback}"),
    ])

    query_generator_llm = query_generator_system_prompt | llm.with_structured_output(Queries)
    state.setdefault("reflection_feedback", "")
    state.setdefault("searched_queries", [])
    configurable = config.get("configurable")

    input_data = {
        **state,
        **configurable  # includes max_queries, search_depth, etc.
    }

    result = query_generator_llm.invoke(input_data, configurable)
    return {"generated_queries": result.queries, "searched_queries": result.queries}


def rag_search_node(state: ResearchState, config: RunnableConfig):
    queries = state["generated_queries"]
    configurable = config.get("configurable")
    search_results = []
    for query in queries:
        raw_content = []
        response = retrieve_from_store(query.query, configurable.get("thread_id"), configurable.get("n_points"))
        for result in response:
            content = f"filename:{result.payload['document']['filename']}\nPage_number:{result.payload['document']['page_number']}\nPage_Content: {result.payload['document']["page_content"]}\n\n\n"
            raw_content.append(content)
        search_results.append(SearchResult(query=query, raw_content=raw_content))
    return {"search_results": search_results}


def result_accumulator_node(state: ResearchState, config: RunnableConfig):
    result_accumulator_system_prompt = ChatPromptTemplate.from_messages([
        SystemMessagePromptTemplate.from_template(RESULT_ACCUMULATOR_SYSTEM_PROMPT_TEMPLATE),
        HumanMessagePromptTemplate.from_template(template="{search_results}"),
    ])

    result_accumulator_llm = result_accumulator_system_prompt | llm
    result = result_accumulator_llm.invoke(state)
    return {"accumulated_content": result.content}


def reflection_feedback_node(state: ResearchState, config: RunnableConfig) -> Command[Literal["final_section_formatter", "query_generator"]]:
    reflection_feedback_system_prompt = ChatPromptTemplate.from_messages([
        SystemMessagePromptTemplate.from_template(REFLECTION_FEEDBACK_SYSTEM_PROMPT_TEMPLATE),
        HumanMessagePromptTemplate.from_template(template="Section: {section}\nAccumulated Content: {accumulated_content}"),
    ])

    reflection_feedback_llm = reflection_feedback_system_prompt | llm.with_structured_output(Feedback)
    reflection_count = state.get("reflection_count", 0)
    configurable = config.get("configurable")
    result = reflection_feedback_llm.invoke(state)
    feedback = result.feedback
    if (feedback == True) or (feedback.lower() == "true") or (reflection_count < configurable.get("num_reflections")):
        return Command(
            update={"reflection_feedback": feedback},
            goto="final_section_formatter"
        )
    else:
        return Command(
            update={"reflection_feedback": feedback, "reflection_count": reflection_count + 1},
            goto="query_generator"
        )
    

def final_section_formatter_node(state: ResearchState, config: RunnableConfig):
    # Format search_results for citations
    formatted_results = []
    for i, sr in enumerate(state.get("search_results", []), 1):
        formatted_results.append(
            f"[{i}] Query: \"{sr.query.query}\"\n"
            f"   Content: \"{' '.join(sr.raw_content[:100])}...\""
        )
    search_results_str = "\n\n".join(formatted_results) if formatted_results else "No search results available."

    final_section_formatter_system_prompt = ChatPromptTemplate.from_messages([
        SystemMessagePromptTemplate.from_template(FINAL_SECTION_FORMATTER_SYSTEM_PROMPT_TEMPLATE),
        HumanMessagePromptTemplate.from_template(template="Internal Knowledge: {knowledge}\nSearch Results: {search_results}\nSearch Result content: {accumulated_content}"),
    ])

    final_section_formatter_llm = final_section_formatter_system_prompt | llm
    result = final_section_formatter_llm.invoke({
        "knowledge": state.get("knowledge", ""),
        "search_results": search_results_str,
        "accumulated_content": state.get("accumulated_content", "")
    })
    return {"final_section_content": [result.content]}


def final_report_writer_node(state: AgentState, config: RunnableConfig):
    final_report_writer_system_prompt = ChatPromptTemplate.from_messages([
        SystemMessagePromptTemplate.from_template(FINAL_REPORT_WRITER_SYSTEM_PROMPT_TEMPLATE),
        HumanMessagePromptTemplate.from_template(template="Report Structure: {report_structure}\nSection Contents: {final_section_content}"),
    ])

    final_report_writer_llm = final_report_writer_system_prompt | llm
    result = final_report_writer_llm.invoke(state)
    return {"final_report_content": result.content}
