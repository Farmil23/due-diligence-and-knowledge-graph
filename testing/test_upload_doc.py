import sys
import os

# Add the project root directory to the path to allow importing the app module
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.graph_extractor import extractor_service

def test_document_extraction():
    # Use the dummy file located in the same testing folder
    current_dir = os.path.dirname(os.path.abspath(__file__))
    file_path = os.path.join(current_dir, "dummy_report.txt")
    
    print(f"🚀 Starting document extraction: {file_path}")
    
    # Run the extraction function from the extractor_service
    success = extractor_service.process_uploaded_file_from_api(
        file_path=file_path, 
        filename="dummy_report.txt"
    )
    
    if success:
        print("\n✅ Document extraction successful! Entities and relationships have been saved to Neo4j.")
    else:
        print("\n❌ Failed to process the document.")

if __name__ == "__main__":
    test_document_extraction()
