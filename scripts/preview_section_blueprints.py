"""Local UI fixture server: synthetic data, in-memory only, no model calls.

Run this on 127.0.0.1:8765 and start the frontend with
REACT_APP_BACKEND_URL=http://127.0.0.1:8765. Any fixture login is accepted.
This app is never mounted by the production server.
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import server
from section_blueprints import catalog, topic_catalog, paper_summary
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000", "http://127.0.0.1:3000", "http://127.0.0.1:3017"], allow_methods=["*"], allow_headers=["*"])
user = {"id": "fixture-teacher", "name": "Preview Teacher", "role": "teacher", "email": "preview@example.test"}
book = {"id": "fixture-physics", "owner_id": user["id"], "original_filename": "Physics Class XII — preview textbook",
        "subject": "Physics", "class_name": "12", "status": "topics_ready", "is_deleted": False,
        "chunks": ["Electromagnetism and transformers. Ray optics and optical instruments."],
        "topics": [{"name": "Electromagnetism", "subtopics": ["Transformers", "Magnetic induction"], "source_chunks": [0]},
                   {"name": "Ray optics", "subtopics": ["Optical instruments"], "source_chunks": [0]}]}
book["topic_catalog"] = topic_catalog(book)
papers = {}
class MemoryCollection:
    async def find_one(self, query, *args):
        return book if query.get("id") == book["id"] else None
    async def update_one(self, *args, **kwargs):
        return None
class MemoryDB:
    textbooks = MemoryCollection()
    paper_instruction_cache = MemoryCollection()
    paper_blueprints = MemoryCollection()
server.db = MemoryDB()

@app.post("/api/auth/login")
async def login():
    return {"token": "local-fixture-only", "user": user}
@app.get("/api/auth/me")
async def me():
    return user
@app.get("/api/textbooks")
async def textbooks():
    return [book]
@app.get("/api/textbooks/{book_id}")
async def textbook(book_id: str):
    return book
@app.get("/api/paper-blueprints")
async def blueprints():
    return await server.get_blueprints(user)
@app.post("/api/papers/configuration-preview")
async def preview(req: server.PaperRequest):
    # Fixture parser does not call a model. Production parser is tested separately.
    import instruction_spec
    from unittest.mock import patch
    with patch.object(instruction_spec, "parse_instructions", AsyncMock(return_value={"unparsed": ["Fixture: free-text instructions are not parsed."]} if req.custom_instructions else {})):
        return await server.configuration_preview(req, user)
@app.post("/api/papers/generate")
async def generate(req: server.PaperRequest):
    result = await preview(req)
    if not result["valid"]:
        raise HTTPException(422, "; ".join(result["errors"]))
    spec = result["requirements"]
    sections = []
    for section in spec["blueprint"]["sections"]:
        questions = []
        for row in spec["section_allocations"]:
            if row["section_id"] != section["id"]:
                continue
            for n in range(row["count"]):
                questions.append({"id": f"{section['id']}-{len(questions)}", "topic": row["name"], "type": "concept",
                    "question": f"Explain how {row['name'].lower()} applies in this example. State the physical principle and justify your reasoning. (Preview question {n + 1}.)",
                    "needs_diagram": False, "internal_choice_count": 0, "important": False,
                    **{k: row[k] for k in ("allocation_id", "topic_id", "textbook_id", "group_id", "difficulty", "marks", "format")},
                    **({"options": ["Principle A", "Principle B", "Principle C", "Principle D"], "correct_option": 1} if row["format"] == "mcq" else {})})
        sections.append({"section_id": section["id"], "title": section["title"], "questions": questions})
    paper = {**req.model_dump(), "id": "fixture-paper", "generation_status": "ready", "generation_requirements": spec,
             "target_question_count": spec["total_questions"], "sections": sections, "instructions": "Synthetic preview. Answer all required sections.", "created_at": "2026-09-09T00:00:00Z"}
    paper["summary"] = paper_summary(paper)
    papers[paper["id"]] = paper
    return paper
@app.get("/api/papers")
async def list_papers():
    return list(papers.values())
@app.get("/api/papers/{paper_id}")
async def paper(paper_id: str):
    return papers[paper_id]
@app.patch("/api/papers/{paper_id}")
async def edit(paper_id: str, body: dict):
    papers[paper_id].update(body)
    papers[paper_id]["summary"] = paper_summary(papers[paper_id])
    return papers[paper_id]
@app.get("/api/stats")
async def stats():
    return {"textbooks": 1, "papers": len(papers), "questions": 0}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8765)
