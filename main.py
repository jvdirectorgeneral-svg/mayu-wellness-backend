from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def read_root():
    return {"message": "Mayu Wellness Backend funcionando 🚀"}

@app.get("/health")
def health_check():
    return {"status": "ok"}
