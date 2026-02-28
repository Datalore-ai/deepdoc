import os
from datetime import datetime
import uuid

from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown
from rich.text import Text
from rich.prompt import Prompt
from rich.table import Table
from pyfiglet import Figlet

from reportlab.platypus import SimpleDocTemplate, Paragraph
from reportlab.lib.styles import getSampleStyleSheet

from deepresearch.graph import graph

console = Console()

def render_banner(title: str = "DeepDoc.ai", subtitle: str = ""):
    """Render the application banner with ASCII art."""
    figlet = Figlet(font="banner3-d", width=200)
    ascii_art = figlet.renderText(title)

    panel = Panel.fit(
        f"[bold bright_magenta]{ascii_art}[/bold bright_magenta]\n[green]{subtitle}[/green]",
        border_style="cyan",
        padding=(1, 2),
        title="[bold yellow]WELCOME[/bold yellow]",
    )

    console.print(panel)


def handle_resource_setup(event_data):
    """Handle resource setup event."""
    console.print(
        Panel(
            "Setting up the database for you...",
            title="RESOURCE SETUP",
            style="yellow",
            width=120
        )
    )


def handle_report_structure_planner(event_data):
    """Handle report structure planner event."""
    try:
        messages = event_data.get("messages", [])
        if messages:
            msg = messages[-1].content
            console.print(
                Panel(
                    Text(msg, style="white"),
                    title="REPORT STRUCTURE PLANNER",
                    style="green",
                    width=120
                )
            )
        else:
            console.print("[yellow]Report structure planner: No messages available[/yellow]")
    except (AttributeError, IndexError) as e:
        console.print(f"[red]Error processing report structure planner: {e}[/red]")


def handle_section_formatter(event_data):
    """Handle section formatter event."""
    try:
        sections = event_data.get("sections", [])
        
        if not sections:
            console.print("[yellow]Section formatter: No sections available[/yellow]")
            return

        table = Table(
            title="Section Formatter",
            show_header=True,
            header_style="bold cyan"
        )
        table.add_column("Section", style="bold yellow")
        table.add_column("Sub-sections", style="green")

        for section in sections:
            section_name = getattr(section, "section_name", "Unknown Section")
            sub_sections = getattr(section, "sub_sections", [])
            
            if sub_sections:
                subs = "\n- " + "\n- ".join(sub_sections)
            else:
                subs = "No sub-sections"
            
            table.add_row(section_name, subs)

        console.print(Panel(table, style="cyan", width=120))
    except Exception as e:
        console.print(f"[red]Error processing section formatter: {e}[/red]")


def handle_research_agent(event_data):
    """Handle research agent event."""
    console.print(
        Panel(
            "Research agent is extracting and processing the required details.",
            title="RESEARCH AGENT",
            style="magenta",
            width=120
        )
    )


def handle_final_report_writer(event_data):
    """Handle final report writer event and export the report."""
    try:
        report = event_data.get("final_report_content")
        
        if not report:
            console.print("[yellow]Final report writer: No report content available[/yellow]")
            return

        console.print(
            Panel(
                Markdown(report),
                title="FINAL REPORT",
                style="bold blue",
                width=200
            )
        )
        
        # Export report to file
        os.makedirs("output_folder", exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        md_filename = f"output_folder/final_report_{timestamp}.md"

        with open(md_filename, "w", encoding="utf-8") as f:
            f.write(report)

        console.print(
            f"[green]Report exported to[/green] [cyan]{md_filename}[/cyan]"
        )
    except Exception as e:
        console.print(f"[red]Error processing final report: {e}[/red]")


def handle_human_feedback(event_data):
    """Handle human feedback event."""
    try:
        messages = event_data.get("messages", [])
        if messages:
            msg = messages[-1].content
            console.print(
                Panel(
                    Text(msg, style="italic white"),
                    title="HUMAN FEEDBACK",
                    style="red",
                    width=120
                )
            )
        else:
            console.print("[yellow]Human feedback: No messages available[/yellow]")
    except (AttributeError, IndexError) as e:
        console.print(f"[red]Error processing human feedback: {e}[/red]")


def handle_unknown_event(event_key, event_data):
    """Handle unknown or unrecognized events."""
    console.print(
        f"[dim]Received unknown event: {event_key}[/dim]"
    )


# Event handler mapping for cleaner dispatch
EVENT_HANDLERS = {
    "resource_setup": handle_resource_setup,
    "report_structure_planner": handle_report_structure_planner,
    "section_formatter": handle_section_formatter,
    "research_agent": handle_research_agent,
    "final_report_writer": handle_final_report_writer,
    "human_feedback": handle_human_feedback,
}


def run_tool(topic, outline, resource_path, config):
    """
    Run the deep research tool with the given parameters.
    
    Args:
        topic: Research topic
        outline: Research outline or goal
        resource_path: Path to resource directory
        config: Thread configuration
    """
    try:
        for event in graph.stream(
            {"topic": topic, "outline": outline, "resource_path": resource_path},
            config=config,
        ):
            # Process each event with appropriate handler
            for event_key, event_data in event.items():
                handler = EVENT_HANDLERS.get(event_key, handle_unknown_event)
                
                if handler == handle_unknown_event:
                    handler(event_key, event_data)
                else:
                    handler(event_data)
    except KeyboardInterrupt:
        console.print("\n[yellow]Process interrupted by user[/yellow]")
    except Exception as e:
        console.print(f"[red]Error during tool execution: {e}[/red]")


if __name__ == "__main__":
    from configuration import THREAD_CONFIG
    
    render_banner("DeepDoc.ai", "AI-powered Local Deep Research")

    topic = Prompt.ask("[bold yellow]Enter your topic[/bold yellow]").strip()
    outline = Prompt.ask("[bold yellow]Enter your outline or goal[/bold yellow]").strip()
    resource_path = Prompt.ask("[bold yellow]Enter the directory path[/bold yellow]").strip()

    run_tool(topic, outline, resource_path, THREAD_CONFIG)
