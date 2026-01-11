"""Command-line interface for the finance assistant."""

from src.models import Action, FinancialParameters, UserInput
from src.workflow import create_assistant_graph
from src.workflow.state import FinanceState


def main():
    """Run the interactive CLI for the finance assistant."""
    print("====================================================")
    print("   Professional MCP Finance Assistant (LLM-Powered)  ")
    print("====================================================")
    print("Capabilities:")
    print(" - List: 'Show my spending on food this week'")
    print(" - Add: 'I spent 45.50 on gadgets today'")
    print(" - Balance: 'What is my total balance right now?'")
    print(" - Delete: 'Delete transaction 4'")
    print(" - Hybrid: 'How much did I spend in the last 48 hours?'")
    print("\nSimulation Tips:")
    print(" - Prefix with 'audio:' for file-based transcription (requires .wav)")
    print(" - Standard text input works directly.")
    print("----------------------------------------------------\n")

    assistant_graph = create_assistant_graph()
    current_history = []

    while True:
        try:
            u_input = input("You: ").strip()
        except EOFError:
            break
        
        if u_input.lower() in ["exit", "quit", "bye"]:
            print("\nAssistant: Goodbye! Tracking your finances is the first step to wealth.")
            break
        
        if not u_input:
            continue
        
        is_audio = False
        text_to_process = u_input
        if u_input.lower().startswith("audio:"):
            is_audio = True
            text_to_process = u_input[6:].strip()
        
        state: FinanceState = {
            "input": UserInput(text=text_to_process, is_audio=is_audio),
            "transcription": None,
            "action": Action.UNKNOWN,
            "parameters": FinancialParameters(),
            "query_results": None,
            "response": None,
            "history": current_history
        }
        
        try:
            result = assistant_graph.invoke(state)
            current_history = result["history"]
            print(f"\nAssistant: {result['response']}\n")
        except Exception as e:
            print(f"\nAssistant: Oops, I ran into a technical issue: {e}\n")


if __name__ == "__main__":
    main()
