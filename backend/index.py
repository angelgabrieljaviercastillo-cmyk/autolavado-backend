from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime
import os
import stripe

app = FastAPI()

# Configuración de CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Conexión a MongoDB (Base de datos del Autolavado)
MONGO_URL = os.getenv("MONGO_URL")
client = AsyncIOMotorClient(MONGO_URL)
db = client.wash_membresia

# Modelos para los Clientes y Servicios
class Socio(BaseModel):
    nombre: str
    telefono: str
    placas: str
    plan_id: str  # Ejemplo: 'Premium', 'Basico'
    fecha_inicio: datetime = datetime.now()

class RegistroLavado(BaseModel):
    placas: str
    tipo_lavado: str
    empleado_id: str

# --- RUTAS DEL SISTEMA ---

@app.get("/")
async def status():
    return {"sistema": "WashMembresia", "estado": "Online"}

# Registrar un nuevo socio (Membresía)
@app.post("/api/socios")
async def registrar_socio(socio: Socio):
    nuevo_socio = await db.socios.insert_one(socio.dict())
    return {"id": str(nuevo_socio.inserted_id), "mensaje": "Socio registrado con éxito"}

# Consultar si un auto tiene lavados disponibles (Validación QR)
@app.get("/api/validar/{placas}")
async def validar_lavado(placas: str):
    socio = await db.socios.find_one({"placas": placas})
    if not socio:
        raise HTTPException(status_code=404, detail="Vehículo no registrado")
    
    # Aquí puedes agregar lógica para contar cuántos lavados lleva en el mes
    lavados_mes = await db.lavados.count_documents({"placas": placas})
    
    return {
        "nombre": socio["nombre"],
        "plan": socio["plan_id"],
        "lavados_realizados": lavados_mes
    }

# Registrar un servicio realizado
@app.post("/api/lavados/registrar")
async def registrar_servicio(lavado: RegistroLavado):
    resultado = await db.lavados.insert_one(lavado.dict())
    return {"status": "Lavado anotado", "id": str(resultado.inserted_id)}
