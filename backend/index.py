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

-- ============================================
-- WashMembresía: Multi-tenant + Roles + Operación
-- ============================================

-- 1. ENUM de roles
CREATE TYPE public.app_role AS ENUM ('admin', 'supervisor', 'empleado');

-- 2. Tabla businesses (negocio = autolavado)
CREATE TABLE public.businesses (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id UUID NOT NULL,
  nombre TEXT NOT NULL,
  telefono TEXT,
  direccion TEXT,
  logo_url TEXT,
  whatsapp TEXT,
  moneda TEXT NOT NULL DEFAULT 'MXN',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 3. Tabla user_roles (roles por negocio - SEPARADA por seguridad)
CREATE TABLE public.user_roles (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL,
  business_id UUID NOT NULL REFERENCES public.businesses(id) ON DELETE CASCADE,
  role app_role NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (user_id, business_id, role)
);

-- 4. Función security definer: ¿el usuario tiene este rol en este negocio?
CREATE OR REPLACE FUNCTION public.has_role(_user_id UUID, _business_id UUID, _role app_role)
RETURNS BOOLEAN
LANGUAGE SQL
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT EXISTS (
    SELECT 1 FROM public.user_roles
    WHERE user_id = _user_id AND business_id = _business_id AND role = _role
  )
$$;

-- 5. Función: ¿el usuario pertenece a este negocio (cualquier rol)?
CREATE OR REPLACE FUNCTION public.belongs_to_business(_user_id UUID, _business_id UUID)
RETURNS BOOLEAN
LANGUAGE SQL
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT EXISTS (
    SELECT 1 FROM public.user_roles
    WHERE user_id = _user_id AND business_id = _business_id
  )
$$;

-- 6. Función: obtener el primer business del usuario (para onboarding/default)
CREATE OR REPLACE FUNCTION public.get_user_business(_user_id UUID)
RETURNS UUID
LANGUAGE SQL
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT business_id FROM public.user_roles
  WHERE user_id = _user_id
  ORDER BY created_at ASC
  LIMIT 1
$$;

-- 7. Trigger: al crear un business, su owner se vuelve admin automáticamente
CREATE OR REPLACE FUNCTION public.assign_owner_admin_role()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
  INSERT INTO public.user_roles (user_id, business_id, role)
  VALUES (NEW.owner_id, NEW.id, 'admin')
  ON CONFLICT DO NOTHING;
  RETURN NEW;
END;
$$;

CREATE TRIGGER on_business_created
AFTER INSERT ON public.businesses
FOR EACH ROW EXECUTE FUNCTION public.assign_owner_admin_role();

-- 8. Tipos de vehículo
CREATE TABLE public.vehicle_types (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id UUID NOT NULL REFERENCES public.businesses(id) ON DELETE CASCADE,
  nombre TEXT NOT NULL,
  icono TEXT,
  orden INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 9. Servicios
CREATE TABLE public.services (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id UUID NOT NULL REFERENCES public.businesses(id) ON DELETE CASCADE,
  nombre TEXT NOT NULL,
  descripcion TEXT,
  duracion_minutos INTEGER DEFAULT 30,
  activo BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 10. Precios por servicio + tipo de vehículo
CREATE TABLE public.service_prices (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  service_id UUID NOT NULL REFERENCES public.services(id) ON DELETE CASCADE,
  vehicle_type_id UUID NOT NULL REFERENCES public.vehicle_types(id) ON DELETE CASCADE,
  precio NUMERIC(10,2) NOT NULL,
  UNIQUE(service_id, vehicle_type_id)
);

-- 11. Clientes finales del autolavado
CREATE TABLE public.customers (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id UUID NOT NULL REFERENCES public.businesses(id) ON DELETE CASCADE,
  nombre TEXT NOT NULL,
  telefono TEXT,
  email TEXT,
  notas TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 12. Vehículos del cliente
CREATE TABLE public.vehicles (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id UUID NOT NULL REFERENCES public.businesses(id) ON DELETE CASCADE,
  customer_id UUID REFERENCES public.customers(id) ON DELETE SET NULL,
  vehicle_type_id UUID REFERENCES public.vehicle_types(id) ON DELETE SET NULL,
  placas TEXT NOT NULL,
  marca TEXT,
  modelo TEXT,
  color TEXT,
  ano INTEGER,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_vehicles_placas ON public.vehicles(business_id, placas);

-- 13. Órdenes (lavados / servicios)
CREATE TYPE public.order_status AS ENUM ('pendiente', 'en_proceso', 'terminado', 'entregado', 'cancelado');
CREATE TYPE public.payment_method AS ENUM ('efectivo', 'tarjeta', 'transferencia', 'membresia', 'otro');

CREATE TABLE public.orders (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id UUID NOT NULL REFERENCES public.businesses(id) ON DELETE CASCADE,
  folio SERIAL,
  customer_id UUID REFERENCES public.customers(id) ON DELETE SET NULL,
  vehicle_id UUID REFERENCES public.vehicles(id) ON DELETE SET NULL,
  empleado_id UUID NOT NULL,
  status order_status NOT NULL DEFAULT 'pendiente',
  metodo_pago payment_method,
  total NUMERIC(10,2) NOT NULL DEFAULT 0,
  notas TEXT,
  pagado BOOLEAN NOT NULL DEFAULT false,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finalizado_at TIMESTAMPTZ
);

-- 14. Servicios incluidos en una orden
CREATE TABLE public.order_services (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  order_id UUID NOT NULL REFERENCES public.orders(id) ON DELETE CASCADE,
  service_id UUID NOT NULL REFERENCES public.services(id) ON DELETE RESTRICT,
  nombre_snapshot TEXT NOT NULL,
  precio NUMERIC(10,2) NOT NULL,
  cantidad INTEGER NOT NULL DEFAULT 1,
  subtotal NUMERIC(10,2) NOT NULL
);

-- 15. Checklist de recepción
CREATE TABLE public.checklist_items (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  order_id UUID NOT NULL REFERENCES public.orders(id) ON DELETE CASCADE,
  categoria TEXT NOT NULL, -- combustible, exterior, interior, accesorios, objetos
  item TEXT NOT NULL,
  presente BOOLEAN,
  estado TEXT, -- bueno, regular, dañado, ausente
  notas TEXT,
  foto_url TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 16. Gastos
CREATE TABLE public.expenses (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id UUID NOT NULL REFERENCES public.businesses(id) ON DELETE CASCADE,
  user_id UUID NOT NULL,
  categoria TEXT NOT NULL,
  descripcion TEXT NOT NULL,
  monto NUMERIC(10,2) NOT NULL,
  fecha DATE NOT NULL DEFAULT CURRENT_DATE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 17. Membresías que el dueño vende a sus clientes
CREATE TABLE public.memberships (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id UUID NOT NULL REFERENCES public.businesses(id) ON DELETE CASCADE,
  nombre TEXT NOT NULL,
  descripcion TEXT,
  precio_mensual NUMERIC(10,2) NOT NULL,
  lavados_incluidos INTEGER NOT NULL,
  servicios_incluidos UUID[] DEFAULT '{}',
  activo BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TYPE public.membership_status AS ENUM ('activa', 'pausada', 'vencida', 'cancelada');

CREATE TABLE public.customer_memberships (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id UUID NOT NULL REFERENCES public.businesses(id) ON DELETE CASCADE,
  customer_id UUID NOT NULL REFERENCES public.customers(id) ON DELETE CASCADE,
  membership_id UUID NOT NULL REFERENCES public.memberships(id) ON DELETE RESTRICT,
  vehicle_id UUID REFERENCES public.vehicles(id) ON DELETE SET NULL,
  status membership_status NOT NULL DEFAULT 'activa',
  inicio DATE NOT NULL DEFAULT CURRENT_DATE,
  proximo_pago DATE NOT NULL,
  lavados_consumidos INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 18. Adaptar tablas existentes con business_id (NULL temporal, luego se llenará)
ALTER TABLE public.planes ADD COLUMN business_id UUID REFERENCES public.businesses(id) ON DELETE CASCADE;
ALTER TABLE public.socios ADD COLUMN business_id UUID REFERENCES public.businesses(id) ON DELETE CASCADE;
ALTER TABLE public.lavados ADD COLUMN business_id UUID REFERENCES public.businesses(id) ON DELETE CASCADE;
ALTER TABLE public.lavados ADD COLUMN order_id UUID REFERENCES public.orders(id) ON DELETE SET NULL;

-- 19. Triggers de updated_at
CREATE TRIGGER update_businesses_updated_at BEFORE UPDATE ON public.businesses
  FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();
CREATE TRIGGER update_customers_updated_at BEFORE UPDATE ON public.customers
  FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();
CREATE TRIGGER update_orders_updated_at BEFORE UPDATE ON public.orders
  FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();
CREATE TRIGGER update_customer_memberships_updated_at BEFORE UPDATE ON public.customer_memberships
  FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();

-- 20. Habilitar RLS
ALTER TABLE public.businesses ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_roles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.vehicle_types ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.services ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.service_prices ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.customers ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.vehicles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.order_services ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.checklist_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.expenses ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.customer_memberships ENABLE ROW LEVEL SECURITY;

-- 21. Reemplazar políticas públicas existentes con políticas multi-tenant
DROP POLICY IF EXISTS "Anyone can delete socios" ON public.socios;
DROP POLICY IF EXISTS "Anyone can insert socios" ON public.socios;
DROP POLICY IF EXISTS "Anyone can update socios" ON public.socios;
DROP POLICY IF EXISTS "Socios are viewable by everyone" ON public.socios;
DROP POLICY IF EXISTS "Anyone can delete lavados" ON public.lavados;
DROP POLICY IF EXISTS "Anyone can insert lavados" ON public.lavados;
DROP POLICY IF EXISTS "Anyone can update lavados" ON public.lavados;
DROP POLICY IF EXISTS "Lavados are viewable by everyone" ON public.lavados;
DROP POLICY IF EXISTS "Planes are viewable by everyone" ON public.planes;

-- 22. Políticas RLS

-- BUSINESSES: el owner ve y edita su negocio; miembros pueden ver
CREATE POLICY "Owners manage their business" ON public.businesses
  FOR ALL USING (auth.uid() = owner_id) WITH CHECK (auth.uid() = owner_id);
CREATE POLICY "Members can view their business" ON public.businesses
  FOR SELECT USING (public.belongs_to_business(auth.uid(), id));

-- USER_ROLES: usuarios ven sus propios roles; admins gestionan roles del negocio
CREATE POLICY "Users see own roles" ON public.user_roles
  FOR SELECT USING (auth.uid() = user_id);
CREATE POLICY "Admins see all roles in business" ON public.user_roles
  FOR SELECT USING (public.has_role(auth.uid(), business_id, 'admin'));
CREATE POLICY "Admins manage roles" ON public.user_roles
  FOR INSERT WITH CHECK (public.has_role(auth.uid(), business_id, 'admin'));
CREATE POLICY "Admins update roles" ON public.user_roles
  FOR UPDATE USING (public.has_role(auth.uid(), business_id, 'admin'));
CREATE POLICY "Admins delete roles" ON public.user_roles
  FOR DELETE USING (public.has_role(auth.uid(), business_id, 'admin'));

-- Helper macro: política estándar "miembro del negocio"
-- VEHICLE_TYPES
CREATE POLICY "Members view vehicle_types" ON public.vehicle_types FOR SELECT USING (public.belongs_to_business(auth.uid(), business_id));
CREATE POLICY "Admins manage vehicle_types" ON public.vehicle_types FOR ALL USING (public.has_role(auth.uid(), business_id, 'admin')) WITH CHECK (public.has_role(auth.uid(), business_id, 'admin'));

-- SERVICES
CREATE POLICY "Members view services" ON public.services FOR SELECT USING (public.belongs_to_business(auth.uid(), business_id));
CREATE POLICY "Admins manage services" ON public.services FOR ALL USING (public.has_role(auth.uid(), business_id, 'admin')) WITH CHECK (public.has_role(auth.uid(), business_id, 'admin'));

-- SERVICE_PRICES (vía service_id)
CREATE POLICY "Members view prices" ON public.service_prices FOR SELECT USING (
  EXISTS (SELECT 1 FROM public.services s WHERE s.id = service_id AND public.belongs_to_business(auth.uid(), s.business_id))
);
CREATE POLICY "Admins manage prices" ON public.service_prices FOR ALL USING (
  EXISTS (SELECT 1 FROM public.services s WHERE s.id = service_id AND public.has_role(auth.uid(), s.business_id, 'admin'))
) WITH CHECK (
  EXISTS (SELECT 1 FROM public.services s WHERE s.id = service_id AND public.has_role(auth.uid(), s.business_id, 'admin'))
);

-- CUSTOMERS
CREATE POLICY "Members manage customers" ON public.customers FOR ALL USING (public.belongs_to_business(auth.uid(), business_id)) WITH CHECK (public.belongs_to_business(auth.uid(), business_id));

-- VEHICLES
CREATE POLICY "Members manage vehicles" ON public.vehicles FOR ALL USING (public.belongs_to_business(auth.uid(), business_id)) WITH CHECK (public.belongs_to_business(auth.uid(), business_id));

-- ORDERS
CREATE POLICY "Members manage orders" ON public.orders FOR ALL USING (public.belongs_to_business(auth.uid(), business_id)) WITH CHECK (public.belongs_to_business(auth.uid(), business_id));

-- ORDER_SERVICES
CREATE POLICY "Members view order_services" ON public.order_services FOR SELECT USING (
  EXISTS (SELECT 1 FROM public.orders o WHERE o.id = order_id AND public.belongs_to_business(auth.uid(), o.business_id))
);
CREATE POLICY "Members manage order_services" ON public.order_services FOR ALL USING (
  EXISTS (SELECT 1 FROM public.orders o WHERE o.id = order_id AND public.belongs_to_business(auth.uid(), o.business_id))
) WITH CHECK (
  EXISTS (SELECT 1 FROM public.orders o WHERE o.id = order_id AND public.belongs_to_business(auth.uid(), o.business_id))
);

-- CHECKLIST_ITEMS
CREATE POLICY "Members manage checklist" ON public.checklist_items FOR ALL USING (
  EXISTS (SELECT 1 FROM public.orders o WHERE o.id = order_id AND public.belongs_to_business(auth.uid(), o.business_id))
) WITH CHECK (
  EXISTS (SELECT 1 FROM public.orders o WHERE o.id = order_id AND public.belongs_to_business(auth.uid(), o.business_id))
);

-- EXPENSES (solo admin/supervisor)
CREATE POLICY "Admin/supervisor view expenses" ON public.expenses FOR SELECT USING (
  public.has_role(auth.uid(), business_id, 'admin') OR public.has_role(auth.uid(), business_id, 'supervisor')
);
CREATE POLICY "Admin/supervisor manage expenses" ON public.expenses FOR ALL USING (
  public.has_role(auth.uid(), business_id, 'admin') OR public.has_role(auth.uid(), business_id, 'supervisor')
) WITH CHECK (
  public.has_role(auth.uid(), business_id, 'admin') OR public.has_role(auth.uid(), business_id, 'supervisor')
);

-- MEMBERSHIPS
CREATE POLICY "Members view memberships" ON public.memberships FOR SELECT USING (public.belongs_to_business(auth.uid(), business_id));
CREATE POLICY "Admins manage memberships" ON public.memberships FOR ALL USING (public.has_role(auth.uid(), business_id, 'admin')) WITH CHECK (public.has_role(auth.uid(), business_id, 'admin'));

-- CUSTOMER_MEMBERSHIPS
CREATE POLICY "Members manage customer_memberships" ON public.customer_memberships FOR ALL USING (public.belongs_to_business(auth.uid(), business_id)) WITH CHECK (public.belongs_to_business(auth.uid(), business_id));

-- Tablas legacy adaptadas
CREATE POLICY "Members view planes" ON public.planes FOR SELECT USING (business_id IS NULL OR public.belongs_to_business(auth.uid(), business_id));
CREATE POLICY "Admins manage planes" ON public.planes FOR ALL USING (business_id IS NULL OR public.has_role(auth.uid(), business_id, 'admin')) WITH CHECK (business_id IS NULL OR public.has_role(auth.uid(), business_id, 'admin'));

CREATE POLICY "Members manage socios" ON public.socios FOR ALL USING (business_id IS NULL OR public.belongs_to_business(auth.uid(), business_id)) WITH CHECK (business_id IS NULL OR public.belongs_to_business(auth.uid(), business_id));

CREATE POLICY "Members manage lavados" ON public.lavados FOR ALL USING (business_id IS NULL OR public.belongs_to_business(auth.uid(), business_id)) WITH CHECK (business_id IS NULL OR public.belongs_to_business(auth.uid(), business_id));

-- 23. Función trigger: al crear un business, sembrar tipos de vehículo y servicios demo
CREATE OR REPLACE FUNCTION public.seed_business_defaults()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  vt_auto UUID;
  vt_suv UUID;
  vt_camioneta UUID;
  vt_moto UUID;
  s_ext UUID;
  s_int UUID;
  s_completo UUID;
  s_premium UUID;
  s_motor UUID;
  s_encerado UUID;
BEGIN
  -- Tipos de vehículo
  INSERT INTO public.vehicle_types (business_id, nombre, icono, orden) VALUES
    (NEW.id, 'Auto', 'car', 1) RETURNING id INTO vt_auto;
  INSERT INTO public.vehicle_types (business_id, nombre, icono, orden) VALUES
    (NEW.id, 'SUV', 'car-front', 2) RETURNING id INTO vt_suv;
  INSERT INTO public.vehicle_types (business_id, nombre, icono, orden) VALUES
    (NEW.id, 'Camioneta', 'truck', 3) RETURNING id INTO vt_camioneta;
  INSERT INTO public.vehicle_types (business_id, nombre, icono, orden) VALUES
    (NEW.id, 'Moto', 'bike', 4) RETURNING id INTO vt_moto;

  -- Servicios
  INSERT INTO public.services (business_id, nombre, descripcion, duracion_minutos) VALUES
    (NEW.id, 'Lavado Exterior', 'Lavado completo de carrocería con shampoo y secado', 20) RETURNING id INTO s_ext;
  INSERT INTO public.services (business_id, nombre, descripcion, duracion_minutos) VALUES
    (NEW.id, 'Lavado Interior', 'Aspirado, limpieza de tablero y vestiduras', 30) RETURNING id INTO s_int;
  INSERT INTO public.services (business_id, nombre, descripcion, duracion_minutos) VALUES
    (NEW.id, 'Lavado Completo', 'Exterior + Interior', 45) RETURNING id INTO s_completo;
  INSERT INTO public.services (business_id, nombre, descripcion, duracion_minutos) VALUES
    (NEW.id, 'Premium Encerado', 'Lavado completo + cera de protección', 75) RETURNING id INTO s_premium;
  INSERT INTO public.services (business_id, nombre, descripcion, duracion_minutos) VALUES
    (NEW.id, 'Lavado de Motor', 'Limpieza profesional del compartimiento del motor', 30) RETURNING id INTO s_motor;
  INSERT INTO public.services (business_id, nombre, descripcion, duracion_minutos) VALUES
    (NEW.id, 'Encerado a Mano', 'Aplicación manual de cera premium', 60) RETURNING id INTO s_encerado;

  -- Precios (auto / suv / camioneta / moto)
  INSERT INTO public.service_prices (service_id, vehicle_type_id, precio) VALUES
    (s_ext, vt_auto, 80), (s_ext, vt_suv, 100), (s_ext, vt_camioneta, 120), (s_ext, vt_moto, 50),
    (s_int, vt_auto, 100), (s_int, vt_suv, 130), (s_int, vt_camioneta, 150), (s_int, vt_moto, 60),
    (s_completo, vt_auto, 160), (s_completo, vt_suv, 200), (s_completo, vt_camioneta, 250), (s_completo, vt_moto, 100),
    (s_premium, vt_auto, 280), (s_premium, vt_suv, 350), (s_premium, vt_camioneta, 420), (s_premium, vt_moto, 180),
    (s_motor, vt_auto, 120), (s_motor, vt_suv, 150), (s_motor, vt_camioneta, 180), (s_motor, vt_moto, 80),
    (s_encerado, vt_auto, 350), (s_encerado, vt_suv, 450), (s_encerado, vt_camioneta, 550), (s_encerado, vt_moto, 200);

  -- Membresías sugeridas que el dueño puede vender a sus clientes
  INSERT INTO public.memberships (business_id, nombre, descripcion, precio_mensual, lavados_incluidos) VALUES
    (NEW.id, 'Básica', '4 lavados exteriores al mes', 400, 4),
    (NEW.id, 'Premium', '8 lavados completos al mes + 1 encerado', 900, 8),
    (NEW.id, 'VIP', 'Lavados ilimitados + 2 premium al mes', 1500, 30);

  RETURN NEW;
END;
$$;

CREATE TRIGGER on_business_seed
AFTER 
