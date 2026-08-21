import os
import shutil
from dotenv import load_dotenv

# Load environment variables before importing anything else
load_dotenv()

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.services.graph_extractor import extractor_service
from main import run_agent

app = FastAPI(
    title="Autonomous Due Diligence API",
    description="API for uploading documents and running investigative queries.",
    version="1.0.0"
)

# Ensure an upload directory exists
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

@app.post("/api/upload")
async def upload_document(file: UploadFile = File(...)):
    """
    Upload a document (PDF or TXT) to be extracted and stored in the Neo4j database.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided.")
        
    file_path = os.path.join(UPLOAD_DIR, file.filename)
    
    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
            
        success = extractor_service.process_uploaded_file_from_api(
            file_path=file_path,
            filename=file.filename
        )
        
        if success:
            return JSONResponse(
                status_code=200, 
                content={"message": "Document successfully extracted and saved to Neo4j.", "filename": file.filename}
            )
        else:
            raise HTTPException(status_code=500, detail="Failed to extract document entities.")
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
        
class QueryRequest(BaseModel):
    question: str
    depth: str = "deep"

@app.post("/api/investigate")
async def investigate_query(request: QueryRequest):
    """
    Run the LangGraph agent to investigate a specific entity.
    """
    try:
        result = run_agent(request.question, request.depth)
        if result:
            return {"status": "success", "report": result.get("answer", "No answer generated.")}
        else:
            raise HTTPException(status_code=500, detail="Agent execution failed.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    # Run the server on port 8000
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
