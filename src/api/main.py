# uvicorn src.api.main:app --reload
import asyncio


from fastapi import FastAPI, HTTPException
from sqlalchemy import text

from nemoguardrails import RailsConfig, LLMRails
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer

from src.pii_redaction.presidio_service import ClinicalPIIRedactor
from src.api.models import ChatMessage, ClinicalQuery, ChatRequest
from src.api.database_connection import get_db_engine

load_dotenv()

app = FastAPI(title="Zero-Trust Clinical RAG API")

# Global state to hold our heavy ML models so they only load once at startup
middleware = {}

@app.on_event("startup")
async def startup_event():
    print(" Booting Enterprise AI Middlewares...")
    
    #Loading presidio (spaCy)
    middleware["redactor"] = ClinicalPIIRedactor()
    
    #Loading NeMo Guardrails (ModernBERT + OpenRouter API)
    config = RailsConfig.from_path("./src/guardrails")
    middleware["rails"] = LLMRails(config)
    
    #Loading local embedding model matching embedding script
    print(" Loading local BioClinical ModernBERT embedding model...")
    middleware["embedder"] = SentenceTransformer('NeuML/bioclinical-modernbert-base-embeddings')
    
    print(" System Ready on port 8000.")


# health check
@app.post("/api/v1/clinical-query")
async def process_clinical_query(query: ClinicalQuery):
    try:
        raw_db_context = f"""
        Patient John Doe (ID: {query.patient_id}) was admitted on March 15th.
        Last recorded Furosemide dosage was 40mg IV. 
        Attending physician: Dr. Gregory House, ID: 20043.
        """

        redactor = middleware["redactor"]
        safe_context = redactor.redact_clinical_context(raw_text=raw_db_context)

        augmented_prompt = f"Clinical Context:\n{safe_context}\n\nUser Question: {query.prompt}"
        
        rails = middleware["rails"]
        response = await rails.generate_async(messages=[{"role": "user", "content": augmented_prompt}])

        return {
            "status": "success",
            "redacted_context_used": safe_context.strip(),
            "llm_response": response['content']
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Chat endpoint
@app.post("/api/v1/chat")
async def process_chat(request: ChatRequest):
    try:
        latest_question = request.messages[-1].content
        engine = get_db_engine()
        
        # creating embedding for the user's question into a 768 dimension vector
        embedder = middleware["embedder"]
        query_vector = embedder.encode(latest_question).tolist()
        
        # native pgvector similarity search on patient_encounters table
        with engine.connect() as conn:
            query = text("""
                SELECT drug, dose_val_rx, dose_unit_rx, route, eventtype, test_name, comments, description 
                FROM patient_encounters 
                WHERE subject_id = :subject_id AND clinical_embedding IS NOT NULL
                ORDER BY clinical_embedding <=> CAST(:query_embedding AS vector)
                LIMIT 5;
            """)
            
            result = conn.execute(query, {
                "subject_id": int(request.patient_id),
                "query_embedding": str(query_vector)
            })
            rows = result.fetchall()
            
            if not rows:
                real_db_context = f"No historical records found for patient {request.patient_id}."
            else:
                context_lines = []
                for row in rows:
                    context_lines.append(
                        f"Drug: {row.drug} ({row.dose_val_rx} {row.dose_unit_rx}), Route: {row.route}, "
                        f"Event: {row.eventtype}, Test: {row.test_name}, Comments: {row.comments}, Diagnosis: {row.description}"
                    )
                real_db_context = f"[Records for Patient ID: {request.patient_id}]\n" + "\n".join(context_lines)

        # redacting real context via Presidio
        redactor = middleware["redactor"]
        safe_context = redactor.redact_clinical_context(raw_text=real_db_context)
        
        # assemble prompt
        augmented_prompt = f"Clinical Context:\n{safe_context}\n\nUser Question: {latest_question}"
        
        # format history for Guardrails
        nemo_history = [{"role": msg.role, "content": msg.content} for msg in request.messages[:-1]]
        nemo_history.append({"role": "user", "content": augmented_prompt})
        
        # routing through Guardrails
        rails = middleware["rails"]
        response = await rails.generate_async(messages=nemo_history)
        
        return {"status": "success", "llm_response": response['content']}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# FETCH UNIQUE PATIENTS (EMBEDDINGS ONLY)
@app.get("/api/v1/patients")
async def get_unique_patients():
    try:
        engine = get_db_engine()
        with engine.connect() as conn:
            # Only fetch patients who actually have generated embeddings
            query = text("""
                SELECT DISTINCT subject_id 
                FROM patient_encounters 
                WHERE subject_id IS NOT NULL AND clinical_embedding IS NOT NULL 
                ORDER BY subject_id;
            """)
            result = conn.execute(query)
            patients = [str(row[0]) for row in result]
            
            return {"patients": patients if patients else ["No embedded patients found"]}
            
    except Exception as e:
        return {"patients": [], "error": str(e)}