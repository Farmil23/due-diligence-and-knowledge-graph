import os
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

from app.services.workflow import build_kyc_graph

# Initialize and compile the LangGraph agent
agent = build_kyc_graph()

def run_agent(question: str, depth: str = "deep"):
    """
    Run the Autonomous Due Diligence Agent.
    
    Args:
        question (str): The entity, company, or person to investigate.
        depth (str): "basic" or "deep" investigation.
    """
    initial_state = {
        "question": question,
        "investigation_depth": depth,
    }
    
    print(f"🚀 Starting Autonomous Due Diligence for: {question}")
    print(f"🔍 Depth: {depth}\n")
    
    # Run the compiled LangGraph workflow
    try:
        result = agent.invoke(initial_state)
        
        print("\n✅ Investigation Complete!")
        print("\n--- Final Answer / Report ---")
        print(result.get("answer", "No answer generated."))
        
        return result
    except Exception as e:
        print(f"\n❌ Error during execution: {e}")
        return None

if __name__ == "__main__":
    # Example usage when running the script directly
    sample_query = "Tolong berikan ringkasan risiko dan dewan direksi dari perusahaan GoTo (Gojek Tokopedia)."
    run_agent(sample_query)
