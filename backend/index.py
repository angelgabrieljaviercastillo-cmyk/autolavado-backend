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

import { createContext, useContext, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { supabase } from "@/integrations/supabase/client";
import { useAuth } from "@/hooks/use-auth";
import type { Database } from "@/integrations/supabase/types";

type Business = Database["public"]["Tables"]["businesses"]["Row"];
type Role = Database["public"]["Enums"]["app_role"];

interface BusinessContextType {
  business: Business | null;
  businessId: string | null;
  role: Role | null;
  loading: boolean;
  hasBusiness: boolean;
  refetch: () => void;
}

const BusinessContext = createContext<BusinessContextType>({
  business: null,
  businessId: null,
  role: null,
  loading: true,
  hasBusiness: false,
  refetch: () => {},
});

export function BusinessProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth();

  const { data, isLoading, refetch } = useQuery({
    queryKey: ["my-business", user?.id],
    queryFn: async () => {
      if (!user) return { business: null, role: null as Role | null };
      const { data: roles } = await supabase
        .from("user_roles")
        .select("business_id, role, businesses(*)")
        .eq("user_id", user.id)
        .order("created_at", { ascending: true })
        .limit(1)
        .maybeSingle();
      if (!roles) return { business: null, role: null as Role | null };
      return {
        business: (roles.businesses as Business) ?? null,
        role: roles.role as Role,
      };
    },
    enabled: !!user,
  });

  return (
    <BusinessContext.Provider
      value={{
        business: data?.business ?? null,
        businessId: data?.business?.id ?? null,
        role: data?.role ?? null,
        loading: isLoading,
        hasBusiness: !!data?.business,
        refetch,
      }}
    >
      {children}
    </BusinessContext.Provider>
  );
}

export const useBusiness = () => useContext(BusinessContext);

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { supabase } from "@/integrations/supabase/client";
import { useBusiness } from "@/hooks/use-business";
import type { Database } from "@/integrations/supabase/types";

type TInsert<T extends keyof Database["public"]["Tables"]> = Database["public"]["Tables"][T]["Insert"];

// ============ VEHICLE TYPES ============
export function useVehicleTypes() {
  const { businessId } = useBusiness();
  return useQuery({
    queryKey: ["vehicle_types", businessId],
    queryFn: async () => {
      const { data, error } = await supabase.from("vehicle_types").select("*").eq("business_id", businessId!).order("orden");
      if (error) throw error;
      return data;
    },
    enabled: !!businessId,
  });
}

// ============ SERVICES ============
export function useServices() {
  const { businessId } = useBusiness();
  return useQuery({
    queryKey: ["services", businessId],
    queryFn: async () => {
      const { data, error } = await supabase
        .from("services")
        .select("*, service_prices(*, vehicle_types(*))")
        .eq("business_id", businessId!)
        .order("nombre");
      if (error) throw error;
      return data;
    },
    enabled: !!businessId,
  });
}

export function useCreateService() {
  const qc = useQueryClient();
  const { businessId } = useBusiness();
  return useMutation({
    mutationFn: async (input: { nombre: string; descripcion?: string; duracion_minutos?: number; precios: { vehicle_type_id: string; precio: number }[] }) => {
      const { data: svc, error } = await supabase
        .from("services")
        .insert({ business_id: businessId!, nombre: input.nombre, descripcion: input.descripcion, duracion_minutos: input.duracion_minutos })
        .select()
        .single();
      if (error) throw error;
      if (input.precios.length) {
        const rows = input.precios.map((p) => ({ service_id: svc.id, vehicle_type_id: p.vehicle_type_id, precio: p.precio }));
        const { error: pe } = await supabase.from("service_prices").insert(rows);
        if (pe) throw pe;
      }
      return svc;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["services"] }),
  });
}

export function useDeleteService() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      const { error } = await supabase.from("services").delete().eq("id", id);
      if (error) throw error;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["services"] }),
  });
}

// ============ CUSTOMERS ============
export function useCustomers() {
  const { businessId } = useBusiness();
  return useQuery({
    queryKey: ["customers", businessId],
    queryFn: async () => {
      const { data, error } = await supabase
        .from("customers")
        .select("*, vehicles(*, vehicle_types(*))")
        .eq("business_id", businessId!)
        .order("nombre");
      if (error) throw error;
      return data;
    },
    enabled: !!businessId,
  });
}

export function useCreateCustomer() {
  const qc = useQueryClient();
  const { businessId } = useBusiness();
  return useMutation({
    mutationFn: async (input: TInsert<"customers"> & { vehicles?: { placas: string; marca?: string; modelo?: string; color?: string; vehicle_type_id?: string }[] }) => {
      const { vehicles, ...customer } = input;
      const { data: c, error } = await supabase
        .from("customers")
        .insert({ ...customer, business_id: businessId! })
        .select()
        .single();
      if (error) throw error;
      if (vehicles?.length) {
        const rows = vehicles.map((v) => ({ ...v, customer_id: c.id, business_id: businessId! }));
        const { error: ve } = await supabase.from("vehicles").insert(rows);
        if (ve) throw ve;
      }
      return c;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["customers"] }),
  });
}

// ============ ORDERS ============
export function useOrders() {
  const { businessId } = useBusiness();
  return useQuery({
    queryKey: ["orders", businessId],
    queryFn: async () => {
      const { data, error } = await supabase
        .from("orders")
        .select("*, customers(*), vehicles(*), order_services(*)")
        .eq("business_id", businessId!)
        .order("created_at", { ascending: false })
        .limit(50);
      if (error) throw error;
      return data;
    },
    enabled: !!businessId,
  });
}

export function useOrder(id: string | undefined) {
  return useQuery({
    queryKey: ["order", id],
    queryFn: async () => {
      const { data, error } = await supabase
        .from("orders")
        .select("*, customers(*), vehicles(*, vehicle_types(*)), order_services(*), checklist_items(*)")
        .eq("id", id!)
        .single();
      if (error) throw error;
      return data;
    },
    enabled: !!id,
  });
}

export function useCreateOrder() {
  const qc = useQueryClient();
  const { businessId } = useBusiness();
  return useMutation({
    mutationFn: async (input: {
      customer_id?: string | null;
      vehicle_id?: string | null;
      empleado_id: string;
      services: { service_id: string; nombre_snapshot: string; precio: number; cantidad: number }[];
      notas?: string;
      checklist?: { categoria: string; item: string; presente?: boolean; estado?: string; notas?: string }[];
    }) => {
      const total = input.services.reduce((s, x) => s + x.precio * x.cantidad, 0);
      const { data: order, error } = await supabase
        .from("orders")
        .insert({
          business_id: businessId!,
          customer_id: input.customer_id ?? null,
          vehicle_id: input.vehicle_id ?? null,
          empleado_id: input.empleado_id,
          total,
          notas: input.notas,
        })
        .select()
        .single();
      if (error) throw error;
      if (input.services.length) {
        const rows = input.services.map((s) => ({
          order_id: order.id,
          service_id: s.service_id,
          nombre_snapshot: s.nombre_snapshot,
          precio: s.precio,
          cantidad: s.cantidad,
          subtotal: s.precio * s.cantidad,
        }));
        const { error: se } = await supabase.from("order_services").insert(rows);
        if (se) throw se;
      }
      if (input.checklist?.length) {
        const rows = input.checklist.map((c) => ({ ...c, order_id: order.id }));
        const { error: ce } = await supabase.from("checklist_items").insert(rows);
        if (ce) throw ce;
      }
      return order;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["orders"] }),
  });
}

export function useUpdateOrderStatus() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, status, pagado, metodo_pago }: { id: string; status?: Database["public"]["Enums"]["order_status"]; pagado?: boolean; metodo_pago?: Database["public"]["Enums"]["payment_method"] }) => {
      const patch: Database["public"]["Tables"]["orders"]["Update"] = {};
      if (status) {
        patch.status = status;
        if (status === "terminado" || status === "entregado") patch.finalizado_at = new Date().toISOString();
      }
      if (pagado !== undefined) patch.pagado = pagado;
      if (metodo_pago) patch.metodo_pago = metodo_pago;
      const { error } = await supabase.from("orders").update(patch).eq("id", id);
      if (error) throw error;
    },
    onSuccess: (_d, v) => {
      qc.invalidateQueries({ queryKey: ["orders"] });
      qc.invalidateQueries({ queryKey: ["order", v.id] });
    },
  });
}

// ============ MEMBERSHIPS ============
export function useMemberships() {
  const { businessId } = useBusiness();
  return useQuery({
    queryKey: ["memberships", businessId],
    queryFn: async () => {
      const { data, error } = await supabase.from("memberships").select("*").eq("business_id", businessId!).order("precio_mensual");
      if (error) throw error;
      return data;
    },
    enabled: !!businessId,
  });
}

export function useCustomerMemberships() {
  const { businessId } = useBusiness();
  return useQuery({
    queryKey: ["customer_memberships", businessId],
    queryFn: async () => {
      const { data, error } = await supabase
        .from("customer_memberships")
        .select("*, customers(*), memberships(*), vehicles(*)")
        .eq("business_id", businessId!)
        .order("created_at", { ascending: false });
      if (error) throw error;
      return data;
    },
    enabled: !!businessId,
  });
}

export function useAssignMembership() {
  const qc = useQueryClient();
  const { businessId } = useBusiness();
  return useMutation({
    mutationFn: async (input: { customer_id: string; membership_id: string; vehicle_id?: string | null }) => {
      const proximo = new Date();
      proximo.setMonth(proximo.getMonth() + 1);
      const { error } = await supabase.from("customer_memberships").insert({
        business_id: businessId!,
        customer_id: input.customer_id,
        membership_id: input.membership_id,
        vehicle_id: input.vehicle_id ?? null,
        proximo_pago: proximo.toISOString().slice(0, 10),
      });
      if (error) throw error;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["customer_memberships"] }),
  });
}

import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger, DialogFooter } from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Plus, User, Car } from "lucide-react";
import { useCustomers, useCreateCustomer, useVehicleTypes } from "@/hooks/use-modules";
import { toast } from "sonner";
import { Skeleton } from "@/components/ui/skeleton";

export default function Clientes() {
  const { data: customers, isLoading } = useCustomers();
  const { data: vts } = useVehicleTypes();
  const createCustomer = useCreateCustomer();
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ nombre: "", telefono: "", email: "" });
  const [vehicle, setVehicle] = useState({ placas: "", marca: "", modelo: "", color: "", vehicle_type_id: "" });

  const handleCreate = async () => {
    if (!form.nombre.trim()) return toast.error("Nombre requerido");
    try {
      await createCustomer.mutateAsync({
        nombre: form.nombre,
        telefono: form.telefono || null,
        email: form.email || null,
        business_id: "", // hook lo sobreescribe
        vehicles: vehicle.placas ? [{ ...vehicle, vehicle_type_id: vehicle.vehicle_type_id || undefined }] : [],
      });
      toast.success("Cliente creado");
      setOpen(false);
      setForm({ nombre: "", telefono: "", email: "" });
      setVehicle({ placas: "", marca: "", modelo: "", color: "", vehicle_type_id: "" });
    } catch (e) {
      toast.error((e as Error).message);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-display font-bold">Clientes y Vehículos</h1>
          <p className="text-sm text-muted-foreground">CRM de tu autolavado</p>
        </div>
        <Dialog open={open} onOpenChange={setOpen}>
          <DialogTrigger asChild><Button><Plus className="h-4 w-4" /> Nuevo cliente</Button></DialogTrigger>
          <DialogContent className="max-w-lg">
            <DialogHeader><DialogTitle>Nuevo cliente</DialogTitle></DialogHeader>
            <div className="space-y-3">
              <div><Label>Nombre *</Label><Input value={form.nombre} onChange={(e) => setForm({ ...form, nombre: e.target.value })} /></div>
              <div className="grid grid-cols-2 gap-2">
                <div><Label>Teléfono</Label><Input value={form.telefono} onChange={(e) => setForm({ ...form, telefono: e.target.value })} /></div>
                <div><Label>Email</Label><Input value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></div>
              </div>
              <div className="border-t pt-3">
                <p className="text-sm font-medium mb-2">Vehículo (opcional)</p>
                <div className="grid grid-cols-2 gap-2">
                  <div><Label>Placas</Label><Input value={vehicle.placas} onChange={(e) => setVehicle({ ...vehicle, placas: e.target.value.toUpperCase() })} /></div>
                  <div>
                    <Label>Tipo</Label>
                    <Select value={vehicle.vehicle_type_id} onValueChange={(v) => setVehicle({ ...vehicle, vehicle_type_id: v })}>
                      <SelectTrigger><SelectValue placeholder="Tipo" /></SelectTrigger>
                      <SelectContent>{vts?.map((vt) => <SelectItem key={vt.id} value={vt.id}>{vt.nombre}</SelectItem>)}</SelectContent>
                    </Select>
                  </div>
                  <div><Label>Marca</Label><Input value={vehicle.marca} onChange={(e) => setVehicle({ ...vehicle, marca: e.target.value })} /></div>
                  <div><Label>Modelo</Label><Input value={vehicle.modelo} onChange={(e) => setVehicle({ ...vehicle, modelo: e.target.value })} /></div>
                  <div className="col-span-2"><Label>Color</Label><Input value={vehicle.color} onChange={(e) => setVehicle({ ...vehicle, color: e.target.value })} /></div>
                </div>
              </div>
            </div>
            <DialogFooter><Button onClick={handleCreate} disabled={createCustomer.isPending}>Crear</Button></DialogFooter>
          </DialogContent>
        </Dialog>
      </div>

      {isLoading ? <Skeleton className="h-40" /> : (
        <div className="grid gap-3 md:grid-cols-2">
          {customers?.map((c) => (
            <Card key={c.id}>
              <CardHeader className="pb-2">
                <div className="flex items-center gap-2">
                  <div className="h-9 w-9 rounded-full bg-primary/10 flex items-center justify-center"><User className="h-4 w-4 text-primary" /></div>
                  <div>
                    <CardTitle className="text-base">{c.nombre}</CardTitle>
                    {c.telefono && <p className="text-xs text-muted-foreground">{c.telefono}</p>}
                  </div>
                </div>
              </CardHeader>
              <CardContent className="space-y-1.5">
                {c.vehicles?.length ? c.vehicles.map((v) => (
                  <div key={v.id} className="flex items-center gap-2 text-sm bg-muted/50 rounded-md px-2 py-1.5">
                    <Car className="h-3.5 w-3.5 text-muted-foreground" />
                    <Badge variant="outline" className="font-mono">{v.placas}</Badge>
                    <span className="text-xs text-muted-foreground truncate">{[v.marca, v.modelo, v.color].filter(Boolean).join(" · ")}</span>
                    {v.vehicle_types && <Badge variant="secondary" className="ml-auto text-xs">{v.vehicle_types.nombre}</Badge>}
                  </div>
                )) : <p className="text-xs text-muted-foreground italic">Sin vehículos</p>}
              </CardContent>
            </Card>
          ))}
          {customers?.length === 0 && <p className="text-sm text-muted-foreground col-span-2 text-center py-8">Aún no tienes clientes. Crea el primero.</p>}
        </div>
      )}
    </div>
  );
}

import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger, DialogFooter } from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Crown, Plus, Calendar } from "lucide-react";
import { useMemberships, useCustomerMemberships, useAssignMembership, useCustomers } from "@/hooks/use-modules";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "sonner";

export default function Membresias() {
  const { data: plans, isLoading } = useMemberships();
  const { data: subs } = useCustomerMemberships();
  const { data: customers } = useCustomers();
  const assign = useAssignMembership();
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ customer_id: "", membership_id: "", vehicle_id: "" });

  const handleAssign = async () => {
    if (!form.customer_id || !form.membership_id) return toast.error("Completa los campos");
    try {
      await assign.mutateAsync({
        customer_id: form.customer_id,
        membership_id: form.membership_id,
        vehicle_id: form.vehicle_id || null,
      });
      toast.success("Membresía asignada");
      setOpen(false);
      setForm({ customer_id: "", membership_id: "", vehicle_id: "" });
    } catch (e) {
      toast.error((e as Error).message);
    }
  };

  const selectedCustomer = customers?.find((c) => c.id === form.customer_id);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div>
          <h1 className="text-2xl font-display font-bold">Membresías</h1>
          <p className="text-sm text-muted-foreground">Planes que vendes a tus clientes</p>
        </div>
        <Dialog open={open} onOpenChange={setOpen}>
          <DialogTrigger asChild><Button><Plus className="h-4 w-4" /> Asignar membresía</Button></DialogTrigger>
          <DialogContent>
            <DialogHeader><DialogTitle>Vender membresía</DialogTitle></DialogHeader>
            <div className="space-y-3">
              <div>
                <Label>Cliente</Label>
                <Select value={form.customer_id} onValueChange={(v) => setForm({ ...form, customer_id: v, vehicle_id: "" })}>
                  <SelectTrigger><SelectValue placeholder="Selecciona" /></SelectTrigger>
                  <SelectContent>{customers?.map((c) => <SelectItem key={c.id} value={c.id}>{c.nombre}</SelectItem>)}</SelectContent>
                </Select>
              </div>
              <div>
                <Label>Plan</Label>
                <Select value={form.membership_id} onValueChange={(v) => setForm({ ...form, membership_id: v })}>
                  <SelectTrigger><SelectValue placeholder="Selecciona" /></SelectTrigger>
                  <SelectContent>{plans?.map((p) => <SelectItem key={p.id} value={p.id}>{p.nombre} — ${p.precio_mensual}/mes</SelectItem>)}</SelectContent>
                </Select>
              </div>
              {selectedCustomer?.vehicles && selectedCustomer.vehicles.length > 0 && (
                <div>
                  <Label>Vehículo (opcional)</Label>
                  <Select value={form.vehicle_id} onValueChange={(v) => setForm({ ...form, vehicle_id: v })}>
                    <SelectTrigger><SelectValue placeholder="Cualquiera" /></SelectTrigger>
                    <SelectContent>{selectedCustomer.vehicles.map((v) => <SelectItem key={v.id} value={v.id}>{v.placas}</SelectItem>)}</SelectContent>
                  </Select>
                </div>
              )}
            </div>
            <DialogFooter><Button onClick={handleAssign} disabled={assign.isPending}>Asignar</Button></DialogFooter>
          </DialogContent>
        </Dialog>
      </div>

      <div>
        <h2 className="text-sm font-semibold text-muted-foreground mb-2 uppercase tracking-wide">Planes disponibles</h2>
        {isLoading ? <Skeleton className="h-32" /> : (
          <div className="grid gap-3 md:grid-cols-3">
            {plans?.map((p) => (
              <Card key={p.id} className="border-2 hover:border-primary/50 transition-colors">
                <CardHeader>
                  <div className="flex items-center gap-2">
                    <Crown className="h-5 w-5 text-primary" />
                    <CardTitle>{p.nombre}</CardTitle>
                  </div>
                  {p.descripcion && <p className="text-xs text-muted-foreground">{p.descripcion}</p>}
                </CardHeader>
                <CardContent>
                  <div className="text-3xl font-bold">${p.precio_mensual}<span className="text-sm font-normal text-muted-foreground">/mes</span></div>
                  <p className="text-sm text-muted-foreground mt-1">{p.lavados_incluidos} lavados</p>
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </div>

      <div>
        <h2 className="text-sm font-semibold text-muted-foreground mb-2 uppercase tracking-wide">Membresías activas</h2>
        <div className="grid gap-2">
          {subs?.map((s) => (
            <Card key={s.id}>
              <CardContent className="p-4 flex items-center justify-between flex-wrap gap-2">
                <div>
                  <p className="font-medium">{s.customers?.nombre}</p>
                  <p className="text-xs text-muted-foreground">{s.memberships?.nombre} {s.vehicles?.placas && `· ${s.vehicles.placas}`}</p>
                </div>
                <div className="flex items-center gap-2">
                  <Badge variant="outline" className="gap-1"><Calendar className="h-3 w-3" />{s.proximo_pago}</Badge>
                  <Badge>{s.lavados_consumidos}/{s.memberships?.lavados_incluidos}</Badge>
                  <Badge variant={s.status === "activa" ? "default" : "secondary"}>{s.status}</Badge>
                </div>
              </CardContent>
            </Card>
          ))}
          {subs?.length === 0 && <p className="text-sm text-muted-foreground text-center py-6">Sin membresías activas. Vende la primera.</p>}
        </div>
      </div>
    </div>
  );
}
import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger, DialogFooter } from "@/components/ui/dialog";
import { Plus, Trash2, Wrench } from "lucide-react";
import { useServices, useCreateService, useDeleteService, useVehicleTypes } from "@/hooks/use-modules";
import { toast } from "sonner";
import { Skeleton } from "@/components/ui/skeleton";

export default function Servicios() {
  const { data: services, isLoading } = useServices();
  const { data: vts } = useVehicleTypes();
  const createSvc = useCreateService();
  const deleteSvc = useDeleteService();
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ nombre: "", descripcion: "", duracion_minutos: 30 });
  const [precios, setPrecios] = useState<Record<string, number>>({});

  const handleCreate = async () => {
    if (!form.nombre.trim()) return toast.error("Nombre requerido");
    const preciosArr = Object.entries(precios).filter(([, p]) => p > 0).map(([vehicle_type_id, precio]) => ({ vehicle_type_id, precio }));
    try {
      await createSvc.mutateAsync({ ...form, precios: preciosArr });
      toast.success("Servicio creado");
      setOpen(false);
      setForm({ nombre: "", descripcion: "", duracion_minutos: 30 });
      setPrecios({});
    } catch (e) {
      toast.error((e as Error).message);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-display font-bold">Servicios y Precios</h1>
          <p className="text-sm text-muted-foreground">Define precios por tipo de vehículo</p>
        </div>
        <Dialog open={open} onOpenChange={setOpen}>
          <DialogTrigger asChild>
            <Button><Plus className="h-4 w-4" /> Nuevo</Button>
          </DialogTrigger>
          <DialogContent className="max-w-lg">
            <DialogHeader><DialogTitle>Nuevo servicio</DialogTitle></DialogHeader>
            <div className="space-y-3">
              <div><Label>Nombre</Label><Input value={form.nombre} onChange={(e) => setForm({ ...form, nombre: e.target.value })} /></div>
              <div><Label>Descripción</Label><Input value={form.descripcion} onChange={(e) => setForm({ ...form, descripcion: e.target.value })} /></div>
              <div><Label>Duración (min)</Label><Input type="number" value={form.duracion_minutos} onChange={(e) => setForm({ ...form, duracion_minutos: +e.target.value })} /></div>
              <div className="space-y-2">
                <Label>Precios por tipo de vehículo</Label>
                {vts?.map((vt) => (
                  <div key={vt.id} className="flex items-center gap-2">
                    <span className="w-28 text-sm">{vt.nombre}</span>
                    <Input type="number" placeholder="0" value={precios[vt.id] ?? ""} onChange={(e) => setPrecios({ ...precios, [vt.id]: +e.target.value })} />
                  </div>
                ))}
              </div>
            </div>
            <DialogFooter><Button onClick={handleCreate} disabled={createSvc.isPending}>Crear</Button></DialogFooter>
          </DialogContent>
        </Dialog>
      </div>

      {isLoading ? <Skeleton className="h-40" /> : (
        <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
          {services?.map((s) => (
            <Card key={s.id}>
              <CardHeader className="pb-2">
                <div className="flex items-start justify-between">
                  <div className="flex items-center gap-2">
                    <div className="h-8 w-8 rounded-md bg-primary/10 flex items-center justify-center"><Wrench className="h-4 w-4 text-primary" /></div>
                    <CardTitle className="text-base">{s.nombre}</CardTitle>
                  </div>
                  <Button size="icon" variant="ghost" onClick={() => deleteSvc.mutate(s.id)}><Trash2 className="h-4 w-4" /></Button>
                </div>
                {s.descripcion && <p className="text-xs text-muted-foreground">{s.descripcion}</p>}
              </CardHeader>
              <CardContent className="pt-0 space-y-1">
                {s.service_prices?.map((p) => (
                  <div key={p.id} className="flex justify-between text-sm">
                    <span className="text-muted-foreground">{p.vehicle_types?.nombre}</span>
                    <Badge variant="secondary">${p.precio}</Badge>
                  </div>
                ))}
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}

{
  "lockfileVersion": 1,
  "configVersion": 1,
  "workspaces": {
    "": {
      "name": "vite_react_shadcn_ts",
      "dependencies": {
        "@hookform/resolvers": "^3.10.0",
        "@lovable.dev/cloud-auth-js": "^1.1.1",
        "@radix-ui/react-accordion": "^1.2.11",
        "@radix-ui/react-alert-dialog": "^1.1.14",
        "@radix-ui/react-aspect-ratio": "^1.1.7",
        "@radix-ui/react-avatar": "^1.1.10",
        "@radix-ui/react-checkbox": "^1.3.2",
        "@radix-ui/react-collapsible": "^1.1.11",
        "@radix-ui/react-context-menu": "^2.2.15",
        "@radix-ui/react-dialog": "^1.1.14",
        "@radix-ui/react-dropdown-menu": "^2.1.15",
        "@radix-ui/react-hover-card": "^1.1.14",
        "@radix-ui/react-label": "^2.1.7",
        "@radix-ui/react-menubar": "^1.1.15",
        "@radix-ui/react-navigation-menu": "^1.2.13",
        "@radix-ui/react-popover": "^1.1.14",
        "@radix-ui/react-progress": "^1.1.7",
        "@radix-ui/react-radio-group": "^1.3.7",
        "@radix-ui/react-scroll-area": "^1.2.9",
        "@radix-ui/react-select": "^2.2.5",
        "@radix-ui/react-separator": "^1.1.7",
        "@radix-ui/react-slider": "^1.3.5",
        "@radix-ui/react-slot": "^1.2.3",
        "@radix-ui/react-switch": "^1.2.5",
        "@radix-ui/react-tabs": "^1.1.12",
        "@radix-ui/react-toast": "^1.2.14",
        "@radix-ui/react-toggle": "^1.1.9",
        "@radix-ui/react-toggle-group": "^1.1.10",
        "@radix-ui/react-tooltip": "^1.2.7",
        "@supabase/supabase-js": "^2.103.0",
        "@tanstack/react-query": "^5.83.0",
        "@types/qrcode": "^1.5.6",
        "class-variance-authority": "^0.7.1",
        "clsx": "^2.1.1",
        "cmdk": "^1.1.1",
        "date-fns": "^3.6.0",
        "embla-carousel-react": "^8.6.0",
        "input-otp": "^1.4.2",
        "lucide-react": "^0.462.0",
        "next-themes": "^0.3.0",
        "qrcode": "^1.5.4",
        "react": "^18.3.1",
        "react-day-picker": "^8.10.1",
        "react-dom": "^18.3.1",
        "react-hook-form": "^7.61.1",
        "react-resizable-panels": "^2.1.9",
        "react-router-dom": "^6.30.1",
        "recharts": "^2.15.4",
        "sonner": "^1.7.4",
        "tailwind-merge": "^2.6.0",
        "tailwindcss-animate": "^1.0.7",
        "vaul": "^0.9.9",
        "zod": "^3.25.76",
      },
      "devDependencies": {
        "@eslint/js": "^9.32.0",
        "@tailwindcss/typography": "^0.5.16",
        "@testing-library/jest-dom": "^6.6.0",
        "@testing-library/react": "^16.0.0",
        "@types/node": "^22.16.5",
        "@types/react": "^18.3.23",
        "@types/react-dom": "^18.3.7",
        "@vitejs/plugin-react-swc": "^3.11.0",
        "autoprefixer": "^10.4.21",
        "eslint": "^9.32.0",
        "eslint-plugin-react-hooks": "^5.2.0",
        "eslint-plugin-react-refresh": "^0.4.20",
        "globals": "^15.15.0",
        "jsdom": "^20.0.3",
        "lovable-tagger": "^1.1.13",
        "postcss": "^8.5.6",
        "tailwindcss": "^3.4.17",
        "typescript": "^5.8.3",
        "typescript-eslint": "^8.38.0",
        "vite": "^5.4.19",
        "vitest": "^3.2.4",
      },
    },
  },
  "packages": {
    "@adobe/css-tools": ["@adobe/css-tools@4.4.4", "", {}, "sha512-Elp+iwUx5rN5+Y8xLt5/GRoG20WGoDCQ/1Fb+1LiGtvwbDavuSk0jhD/eZdckHAuzcDzccnkv+rEjyWfRx18gg=="],

    "@alloc/quick-lru": ["@alloc/quick-lru@5.2.0", "", {}, "sha512-UrcABB+4bUrFABwbluTIBErXwvbsU/V7TZWfmbgJfbkwiBuziS9gxdODUyuiecfdGQ85jglMW6juS3+z5TsKLw=="],

    "@babel/runtime": ["@babel/runtime@7.28.2", "", {}, "sha512-KHp2IflsnGywDjBWDkR9iEqiWSpc8GIi0lgTT3mOElT0PP1tG26P4tmFI2YvAdzgq9RGyoHZQEIEdZy6Ec5xCA=="],

    "@esbuild/aix-ppc64": ["@esbuild/aix-ppc64@0.25.0", "", { "os": "aix", "cpu": "ppc64" }, "sha512-O7vun9Sf8DFjH2UtqK8Ku3LkquL9SZL8OLY1T5NZkA34+wG3OQF7cl4Ql8vdNzM6fzBbYfLaiRLIOZ+2FOCgBQ=="],

    "@esbuild/android-arm": ["@esbuild/android-arm@0.25.0", "", { "os": "android", "cpu": "arm" }, "sha512-PTyWCYYiU0+1eJKmw21lWtC+d08JDZPQ5g+kFyxP0V+es6VPPSUhM6zk8iImp2jbV6GwjX4pap0JFbUQN65X1g=="],

    "@esbuild/android-arm64": ["@esbuild/android-arm64@0.25.0", "", { "os": "android", "cpu": "arm64" }, "sha512-grvv8WncGjDSyUBjN9yHXNt+cq0snxXbDxy5pJtzMKGmmpPxeAmAhWxXI+01lU5rwZomDgD3kJwulEnhTRUd6g=="],

    "@esbuild/android-x64": ["@esbuild/android-x64@0.25.0", "", { "os": "android", "cpu": "x64" }, "sha512-m/ix7SfKG5buCnxasr52+LI78SQ+wgdENi9CqyCXwjVR2X4Jkz+BpC3le3AoBPYTC9NHklwngVXvbJ9/Akhrfg=="],

    "@esbuild/darwin-arm64": ["@esbuild/darwin-arm64@0.25.0", "", { "os": "darwin", "cpu": "arm64" }, "sha512-mVwdUb5SRkPayVadIOI78K7aAnPamoeFR2bT5nszFUZ9P8UpK4ratOdYbZZXYSqPKMHfS1wdHCJk1P1EZpRdvw=="],

    "@esbuild/darwin-x64": ["@esbuild/darwin-x64@0.25.0", "", { "os": "darwin", "cpu": "x64" }, "sha512-DgDaYsPWFTS4S3nWpFcMn/33ZZwAAeAFKNHNa1QN0rI4pUjgqf0f7ONmXf6d22tqTY+H9FNdgeaAa+YIFUn2Rg=="],

    "@esbuild/freebsd-arm64": ["@esbuild/freebsd-arm64@0.25.0", "", { "os": "freebsd", "cpu": "arm64" }, "sha512-VN4ocxy6dxefN1MepBx/iD1dH5K8qNtNe227I0mnTRjry8tj5MRk4zprLEdG8WPyAPb93/e4pSgi1SoHdgOa4w=="],

    "@esbuild/freebsd-x64": ["@esbuild/freebsd-x64@0.25.0", "", { "os": "freebsd", "cpu": "x64" }, "sha512-mrSgt7lCh07FY+hDD1TxiTyIHyttn6vnjesnPoVDNmDfOmggTLXRv8Id5fNZey1gl/V2dyVK1VXXqVsQIiAk+A=="],

    "@esbuild/linux-arm": ["@esbuild/linux-arm@0.25.0", "", { "os": "linux", "cpu": "arm" }, "sha512-vkB3IYj2IDo3g9xX7HqhPYxVkNQe8qTK55fraQyTzTX/fxaDtXiEnavv9geOsonh2Fd2RMB+i5cbhu2zMNWJwg=="],

    "@esbuild/linux-arm64": ["@esbuild/linux-arm64@0.25.0", "", { "os": "linux", "cpu": "arm64" }, "sha512-9QAQjTWNDM/Vk2bgBl17yWuZxZNQIF0OUUuPZRKoDtqF2k4EtYbpyiG5/Dk7nqeK6kIJWPYldkOcBqjXjrUlmg=="],

    "@esbuild/linux-ia32": ["@esbuild/linux-ia32@0.25.0", "", { "os": "linux", "cpu": "ia32" }, "sha512-43ET5bHbphBegyeqLb7I1eYn2P/JYGNmzzdidq/w0T8E2SsYL1U6un2NFROFRg1JZLTzdCoRomg8Rvf9M6W6Gg=="],

    "@esbuild/linux-loong64": ["@esbuild/linux-loong64@0.25.0", "", { "os": "linux", "cpu": "none" }, "sha512-fC95c/xyNFueMhClxJmeRIj2yrSMdDfmqJnyOY4ZqsALkDrrKJfIg5NTMSzVBr5YW1jf+l7/cndBfP3MSDpoHw=="],

    "@esbuild/linux-mips64el": ["@esbuild/linux-mips64el@0.25.0", "", { "os": "linux", "cpu": "none" }, "sha512-nkAMFju7KDW73T1DdH7glcyIptm95a7Le8irTQNO/qtkoyypZAnjchQgooFUDQhNAy4iu08N79W4T4pMBwhPwQ=="],

    "@esbuild/linux-ppc64": ["@esbuild/linux-ppc64@0.25.0", "", { "os": "linux", "cpu": "ppc64" }, "sha512-NhyOejdhRGS8Iwv+KKR2zTq2PpysF9XqY+Zk77vQHqNbo/PwZCzB5/h7VGuREZm1fixhs4Q/qWRSi5zmAiO4Fw=="],

    "@esbuild/linux-riscv64": ["@esbuild/linux-riscv64@0.25.0", "", { "os": "linux", "cpu": "none" }, "sha512-5S/rbP5OY+GHLC5qXp1y/Mx//e92L1YDqkiBbO9TQOvuFXM+iDqUNG5XopAnXoRH3FjIUDkeGcY1cgNvnXp/kA=="],

    "@esbuild/linux-s390x": ["@esbuild/linux-s390x@0.25.0", "", { "os": "linux", "cpu": "s390x" }, "sha512-XM2BFsEBz0Fw37V0zU4CXfcfuACMrppsMFKdYY2WuTS3yi8O1nFOhil/xhKTmE1nPmVyvQJjJivgDT+xh8pXJA=="],

    "@esbuild/linux-x64": ["@esbuild/linux-x64@0.25.0", "", { "os": "linux", "cpu": "x64" }, "sha512-9yl91rHw/cpwMCNytUDxwj2XjFpxML0y9HAOH9pNVQDpQrBxHy01Dx+vaMu0N1CKa/RzBD2hB4u//nfc+Sd3Cw=="],

    "@esbuild/netbsd-arm64": ["@esbuild/netbsd-arm64@0.25.0", "", { "os": "none", "cpu": "arm64" }, "sha512-RuG4PSMPFfrkH6UwCAqBzauBWTygTvb1nxWasEJooGSJ/NwRw7b2HOwyRTQIU97Hq37l3npXoZGYMy3b3xYvPw=="],

    "@esbuild/netbsd-x64": ["@esbuild/netbsd-x64@0.25.0", "", { "os": "none", "cpu": "x64" }, "sha512-jl+qisSB5jk01N5f7sPCsBENCOlPiS/xptD5yxOx2oqQfyourJwIKLRA2yqWdifj3owQZCL2sn6o08dBzZGQzA=="],

    "@esbuild/openbsd-arm64": ["@esbuild/openbsd-arm64@0.25.0", "", { "os": "openbsd", "cpu": "arm64" }, "sha512-21sUNbq2r84YE+SJDfaQRvdgznTD8Xc0oc3p3iW/a1EVWeNj/SdUCbm5U0itZPQYRuRTW20fPMWMpcrciH2EJw=="],

    "@esbuild/openbsd-x64": ["@esbuild/openbsd-x64@0.25.0", "", { "os": "openbsd", "cpu": "x64" }, "sha512-2gwwriSMPcCFRlPlKx3zLQhfN/2WjJ2NSlg5TKLQOJdV0mSxIcYNTMhk3H3ulL/cak+Xj0lY1Ym9ysDV1igceg=="],

    "@esbuild/sunos-x64": ["@esbuild/sunos-x64@0.25.0", "", { "os": "sunos", "cpu": "x64" }, "sha512-bxI7ThgLzPrPz484/S9jLlvUAHYMzy6I0XiU1ZMeAEOBcS0VePBFxh1JjTQt3Xiat5b6Oh4x7UC7IwKQKIJRIg=="],

    "@esbuild/win32-arm64": ["@esbuild/win32-arm64@0.25.0", "", { "os": "win32", "cpu": "arm64" }, "sha512-ZUAc2YK6JW89xTbXvftxdnYy3m4iHIkDtK3CLce8wg8M2L+YZhIvO1DKpxrd0Yr59AeNNkTiic9YLf6FTtXWMw=="],

    "@esbuild/win32-ia32": ["@esbuild/win32-ia32@0.25.0", "", { "os": "win32", "cpu": "ia32" }, "sha512-eSNxISBu8XweVEWG31/JzjkIGbGIJN/TrRoiSVZwZ6pkC6VX4Im/WV2cz559/TXLcYbcrDN8JtKgd9DJVIo8GA=="],

    "@esbuild/win32-x64": ["@esbuild/win32-x64@0.25.0", "", { "os": "win32", "cpu": "x64" }, "sha512-ZENoHJBxA20C2zFzh6AI4fT6RraMzjYw4xKWemRTRmRVtN9c5DcH9r/f2ihEkMjOW5eGgrwCslG/+Y/3bL+DHQ=="],

    "@eslint-community/eslint-utils": ["@eslint-community/eslint-utils@4.7.0", "", { "dependencies": { "eslint-visitor-keys": "^3.4.3" }, "peerDependencies": { "eslint": "^6.0.0 || ^7.0.0 || >=8.0.0" } }, "sha512-dyybb3AcajC7uha6CvhdVRJqaKyn7w2YKqKyAN37NKYgZT36w+iRb0Dymmc5qEJ549c/S31cMMSFd75bteCpCw=="],

    "@eslint-community/regexpp": ["@eslint-community/regexpp@4.12.1", "", {}, "sha512-CCZCDJuduB9OUkFkY2IgppNZMi2lBQgD2qzwXkEia16cge2pijY/aXi96CJMquDMn3nJdlPV1A5KrJEXwfLNzQ=="],

    "@eslint/config-array": ["@eslint/config-array@0.21.0", "", { "dependencies": { "@eslint/object-schema": "^2.1.6", "debug": "^4.3.1", "minimatch": "^3.1.2" } }, "sha512-ENIdc4iLu0d93HeYirvKmrzshzofPw6VkZRKQGe9Nv46ZnWUzcF1xV01dcvEg/1wXUR61OmmlSfyeyO7EvjLxQ=="],

    "@eslint/config-helpers": ["@eslint/config-helpers@0.3.0", "", {}, "sha512-ViuymvFmcJi04qdZeDc2whTHryouGcDlaxPqarTD0ZE10ISpxGUVZGZDx4w01upyIynL3iu6IXH2bS1NhclQMw=="],

    "@eslint/core": ["@eslint/core@0.15.1", "", { "dependencies": { "@types/json-schema": "^7.0.15" } }, "sha512-bkOp+iumZCCbt1K1CmWf0R9pM5yKpDv+ZXtvSyQpudrI9kuFLp+bM2WOPXImuD/ceQuaa8f5pj93Y7zyECIGNA=="],

    "@eslint/eslintrc": ["@eslint/eslintrc@3.3.1", "", { "dependencies": { "ajv": "^6.12.4", "debug": "^4.3.2", "espree": "^10.0.1", "globals": "^14.0.0", "ignore": "^5.2.0", "import-fresh": "^3.2.1", "js-yaml": "^4.1.0", "minimatch": "^3.1.2", "strip-json-comments": "^3.1.1" } }, "sha512-gtF186CXhIl1p4pJNGZw8Yc6RlshoePRvE0X91oPGb3vZ8pM3qOS9W9NGPat9LziaBV7XrJWGylNQXkGcnM3IQ=="],

    "@eslint/js": ["@eslint/js@9.32.0", "", {}, "sha512-BBpRFZK3eX6uMLKz8WxFOBIFFcGFJ/g8XuwjTHCqHROSIsopI+ddn/d5Cfh36+7+e5edVS8dbSHnBNhrLEX0zg=="],

    "@eslint/object-schema": ["@eslint/object-schema@2.1.6", "", {}, "sha512-RBMg5FRL0I0gs51M/guSAj5/e14VQ4tpZnQNWwuDT66P14I43ItmPfIZRhO9fUVIPOAQXU47atlywZ/czoqFPA=="],

    "@eslint/plugin-kit": ["@eslint/plugin-kit@0.3.4", "", { "dependencies": { "@eslint/core": "^0.15.1", "levn": "^0.4.1" } }, "sha512-Ul5l+lHEcw3L5+k8POx6r74mxEYKG5kOb6Xpy2gCRW6zweT6TEhAf8vhxGgjhqrd/VO/Dirhsb+1hNpD1ue9hw=="],

    "@floating-ui/core": ["@floating-ui/core@1.7.2", "", { "dependencies": { "@floating-ui/utils": "^0.2.10" } }, "sha512-wNB5ooIKHQc+Kui96jE/n69rHFWAVoxn5CAzL1Xdd8FG03cgY3MLO+GF9U3W737fYDSgPWA6MReKhBQBop6Pcw=="],

    "@floating-ui/dom": ["@floating-ui/dom@1.7.2", "", { "dependencies": { "@floating-ui/core": "^1.7.2", "@floating-ui/utils": "^0.2.10" } }, "sha512-7cfaOQuCS27HD7DX+6ib2OrnW+b4ZBwDNnCcT0uTyidcmyWb03FnQqJybDBoCnpdxwBSfA94UAYlRCt7mV+TbA=="],

    "@floating-ui/react-dom": ["@floating-ui/react-dom@2.1.4", "", { "dependencies": { "@floating-ui/dom": "^1.7.2" }, "peerDependencies": { "react": ">=16.8.0", "react-dom": ">=16.8.0" } }, "sha512-JbbpPhp38UmXDDAu60RJmbeme37Jbgsm7NrHGgzYYFKmblzRUh6Pa641dII6LsjwF4XlScDrde2UAzDo/b9KPw=="],

    "@floating-ui/utils": ["@floating-ui/utils@0.2.10", "", {}, "sha512-aGTxbpbg8/b5JfU1HXSrbH3wXZuLPJcNEcZQFMxLs3oSzgtVu6nFPkbbGGUvBcUjKV2YyB9Wxxabo+HEH9tcRQ=="],

    "@hookform/resolvers": ["@hookform/resolvers@3.10.0", "", { "peerDependencies": { "react-hook-form": "^7.0.0" } }, "sha512-79Dv+3mDF7i+2ajj7SkypSKHhl1cbln1OGavqrsF7p6mbUv11xpqpacPsGDCTRvCSjEEIez2ef1NveSVL3b0Ag=="],

    "@humanfs/core": ["@humanfs/core@0.19.1", "", {}, "sha512-5DyQ4+1JEUzejeK1JGICcideyfUbGixgS9jNgex5nqkW+cY7WZhxBigmieN5Qnw9ZosSNVC9KQKyb+GUaGyKUA=="],

    "@humanfs/node": ["@humanfs/node@0.16.6", "", { "dependencies": { "@humanfs/core": "^0.19.1", "@humanwhocodes/retry": "^0.3.0" } }, "sha512-YuI2ZHQL78Q5HbhDiBA1X4LmYdXCKCMQIfw0pw7piHJwyREFebJUvrQN4cMssyES6x+vfUbx1CIpaQUKYdQZOw=="],

    "@humanwhocodes/module-importer": ["@humanwhocodes/module-importer@1.0.1", "", {}, "sha512-bxveV4V8v5Yb4ncFTT3rPSgZBOpCkjfK0y4oVVVJwIuDVBRMDXrPyXRL988i5ap9m9bnyEEjWfm5WkBmtffLfA=="],

    "@humanwhocodes/retry": ["@humanwhocodes/retry@0.4.3", "", {}, "sha512-bV0Tgo9K4hfPCek+aMAn81RppFKv2ySDQeMoSZuvTASywNTnVJCArCZE2FWqpvIatKu7VMRLWlR1EazvVhDyhQ=="],

    "@isaacs/cliui": ["@isaacs/cliui@8.0.2", "", { "dependencies": { "string-width": "^5.1.2", "string-width-cjs": "npm:string-width@^4.2.0", "strip-ansi": "^7.0.1", "strip-ansi-cjs": "npm:strip-ansi@^6.0.1", "wrap-ansi": "^8.1.0", "wrap-ansi-cjs": "npm:wrap-ansi@^7.0.0" } }, "sha512-O8jcjabXaleOG9DQ0+ARXWZBTfnP4WNAqzuiJK7ll44AmxGKv/J2M4TPjxjY3znBCfvBXFzucm1twdyFybFqEA=="],

    "@jridgewell/gen-mapping": ["@jridgewell/gen-mapping@0.3.5", "", { "dependencies": { "@jridgewell/set-array": "^1.2.1", "@jridgewell/sourcemap-codec": "^1.4.10", "@jridgewell/trace-mapping": "^0.3.24" } }, "sha512-IzL8ZoEDIBRWEzlCcRhOaCupYyN5gdIK+Q6fbFdPDg6HqX6jpkItn7DFIpW9LQzXG6Df9sA7+OKnq0qlz/GaQg=="],

    "@jridgewell/resolve-uri": ["@jridgewell/resolve-uri@3.1.2", "", {}, "sha512-bRISgCIjP20/tbWSPWMEi54QVPRZExkuD9lJL+UIxUKtwVJA8wW1Trb1jMs1RFXo1CBTNZ/5hpC9QvmKWdopKw=="],

    "@jridgewell/set-array": ["@jridgewell/set-array@1.2.1", "", {}, "sha512-R8gLRTZeyp03ymzP/6Lil/28tGeGEzhx1q2k703KGWRAI1VdvPIXdG70VJc2pAMw3NA6JKL5hhFu1sJX0Mnn/A=="],

    "@jridgewell/sourcemap-codec": ["@jridgewell/sourcemap-codec@1.5.5", "", {}, "sha512-cYQ9310grqxueWbl+WuIUIaiUaDcj7WOq5fVhEljNVgRfOUhY9fy2zTvfoqWsnebh8Sl70VScFbICvJnLKB0Og=="],

    "@jridgewell/trace-mapping": ["@jridgewell/trace-mapping@0.3.25", "", { "dependencies": { "@jridgewell/resolve-uri": "^3.1.0", "@jridgewell/sourcemap-codec": "^1.4.14" } }, "sha512-vNk6aEwybGtawWmy/PzwnGDOjCkLWSD2wqvjGGAgOAwCGWySYXfYoxt00IJkTF+8Lb57DwOb3Aa0o9CApepiYQ=="],

    "@lovable.dev/cloud-auth-js": ["@lovable.dev/cloud-auth-js@1.1.1", "", {}, "sha512-80elU8dSJG6bho0Xnfj2oy53wp883nYXrG1Wy948LC/ZUaUQ0i9EGXQFmwTLOBFrWqxb6aNaOlZUvQ8BVGhjMQ=="],

    "@nodelib/fs.scandir": ["@nodelib/fs.scandir@2.1.5", "", { "dependencies": { "@nodelib/fs.stat": "2.0.5", "run-parallel": "^1.1.9" } }, "sha512-vq24Bq3ym5HEQm2NKCr3yXDwjc7vTsEThRDnkp2DK9p1uqLR+DHurm/NOTo0KG7HYHU7eppKZj3MyqYuMBf62g=="],

    "@nodelib/fs.stat": ["@nodelib/fs.stat@2.0.5", "", {}, "sha512-RkhPPp2zrqDAQA/2jNhnztcPAlv64XdhIp7a7454A5ovI7Bukxgt7MX7udwAu3zg1DcpPU0rz3VV1SeaqvY4+A=="],

    "@nodelib/fs.walk": ["@nodelib/fs.walk@1.2.8", "", { "dependencies": { "@nodelib/fs.scandir": "2.1.5", "fastq": "^1.6.0" } }, "sha512-oGB+UxlgWcgQkgwo8GcEGwemoTFt3FIO9ababBmaGwXIoBKZ+GTy0pP185beGg7Llih/NSHSV2XAs1lnznocSg=="],

    "@pkgjs/parseargs": ["@pkgjs/parseargs@0.11.0", "", {}, "sha512-+1VkjdD0QBLPodGrJUeqarH8VAIvQODIbwh9XpP5Syisf7YoQgsJKPNFoqqLQlu+VQ/tVSshMR6loPMn8U+dPg=="],

    "@radix-ui/number": ["@radix-ui/number@1.1.1", "", {}, "sha512-MkKCwxlXTgz6CFoJx3pCwn07GKp36+aZyu/u2Ln2VrA5DcdyCZkASEDBTd8x5whTQQL5CiYf4prXKLcgQdv29g=="],

    "@radix-ui/primitive": ["@radix-ui/primitive@1.1.2", "", {}, "sha512-XnbHrrprsNqZKQhStrSwgRUQzoCI1glLzdw79xiZPoofhGICeZRSQ3dIxAKH1gb3OHfNf4d6f+vAv3kil2eggA=="],

    "@radix-ui/react-accordion": ["@radix-ui/react-accordion@1.2.11", "", { "dependencies": { "@radix-ui/primitive": "1.1.2", "@radix-ui/react-collapsible": "1.1.11", "@radix-ui/react-collection": "1.1.7", "@radix-ui/react-compose-refs": "1.1.2", "@radix-ui/react-context": "1.1.2", "@radix-ui/react-direction": "1.1.1", "@radix-ui/react-id": "1.1.1", "@radix-ui/react-primitive": "2.1.3", "@radix-ui/react-use-controllable-state": "1.2.2" }, "peerDependencies": { "@types/react": "*", "@types/react-dom": "*", "react": "^16.8 || ^17.0 || ^18.0 || ^19.0 || ^19.0.0-rc", "react-dom": "^16.8 || ^17.0 || ^18.0 || ^19.0 || ^19.0.0-rc" } }, "sha512-l3W5D54emV2ues7jjeG1xcyN7S3jnK3zE2zHqgn0CmMsy9lNJwmgcrmaxS+7ipw15FAivzKNzH3d5EcGoFKw0A=="],

    "@radix-ui/react-alert-dialog": ["@radix-ui/react-alert-dialog@1.1.14", "", { "dependencies": { "@radix-ui/primitive": "1.1.2", "@radix-ui/react-compose-refs": "1.1.2", "@radix-ui/react-context": "1.1.2", "@radix-ui/react-dialog": "1.1.14", "@radix-ui/react-primitive": "2.1.3", "@radix-ui/react-slot": "1.2.3" }, "peerDependencies": { "@types/react": "*", "@types/react-dom": "*", "react": "^16.8 || ^17.0 || ^18.0 || ^19.0 || ^19.0.0-rc", "react-dom": "^16.8 || ^17.0 || ^18.0 || ^19.0 || ^19.0.0-rc" } }, "sha512-IOZfZ3nPvN6lXpJTBCunFQPRSvK8MDgSc1FB85xnIpUKOw9en0dJj8JmCAxV7BiZdtYlUpmrQjoTFkVYtdoWzQ=="],

    "@radix-ui/react-arrow": ["@radix-ui/react-arrow@1.1.7", "", { "dependencies": { "@radix-ui/react-primitive": "2.1.3" }, "peerDependencies": { "@types/react": "*", "@types/react-dom": "*", "react": "^16.8 || ^17.0 || ^18.0 || ^19.0 || ^19.0.0-rc", "react-dom": "^16.8 || ^17.0 || ^18.0 || ^19.0 || ^19.0.0-rc" } }, "sha512-F+M1tLhO+mlQaOWspE8Wstg+z6PwxwRd8oQ8IXceWz92kfAmalTRf0EjrouQeo7QssEPfCn05B4Ihs1K9WQ/7w=="],

    "@radix-ui/react-aspect-ratio": ["@radix-ui/react-aspect-ratio@1.1.7", "", { "dependencies": { "@radix-ui/react-primitive": "2.1.3" }, "peerDependencies": { "@types/react": "*", "@types/react-dom": "*", "react": "^16.8 || ^17.0 || ^18.0 || ^19.0 || ^19.0.0-rc", "react-dom": "^16.8 || ^17.0 || ^18.0 || ^19.0 || ^19.0.0-rc" } }, "sha512-Yq6lvO9HQyPwev1onK1daHCHqXVLzPhSVjmsNjCa2Zcxy2f7uJD2itDtxknv6FzAKCwD1qQkeVDmX/cev13n/g=="],

    "@radix-ui/react-avatar": ["@radix-ui/react-avatar@1.1.10", "", { "dependencies": { "@radix-ui/react-context": "1.1.2", "@radix-ui/react-primitive": "2.1.3", "@radix-ui/react-use-callback-ref": "1.1.1", "@radix-ui/react-use-is-hydrated": "0.1.0", "@radix-ui/react-use-layout-effect": "1.1.1" }, "peerDependencies": { "@types/react": "*", "@types/react-dom": "*", "react": "^16.8 || ^17.0 || ^18.0 || ^19.0 || ^19.0.0-rc", "react-dom": "^16.8 || ^17.0 || ^18.0 || ^19.0 || ^19.0.0-rc" } }, "sha512-V8piFfWapM5OmNCXTzVQY+E1rDa53zY+MQ4Y7356v4fFz6vqCyUtIz2rUD44ZEdwg78/jKmMJHj07+C/Z/rcog=="],

    "@radix-ui/react-checkbox": ["@radix-ui/react-checkbox@1.3.2", "", { "dependencies": { "@radix-ui/primitive": "1.1.2", "@radix-ui/react-compose-refs": "1.1.2", "@radix-ui/react-context": "1.1.2", "@radix-ui/react-presence": "1.1.4", "@radix-ui/react-primitive": "2.1.3", "@radix-ui/react-use-controllable-state": "1.2.2", "@radix-ui/react-use-previous": "1.1.1", "@radix-ui/react-use-size": "1.1.1" }, "peerDependencies": { "@types/react": "*", "@types/react-dom": "*", "react": "^16.8 || ^17.0 || ^18.0 || ^19.0 || ^19.0.0-rc", "react-dom": "^16.8 || ^17.0 || ^18.0 || ^19.0 || ^19.0.0-rc" } }, "sha512-yd+dI56KZqawxKZrJ31eENUwqc1QSqg4OZ15rybGjF2ZNwMO+wCyHzAVLRp9qoYJf7kYy0YpZ2b0JCzJ42HZpA=="],

    "@radix-ui/react-collapsible": ["@radix-ui/react-collapsible@1.1.11", "", { "dependencies": { "@radix-ui/primitive": "1.1.2", "@radix-ui/react-compose-refs": "1.1.2", "@radix-ui/react-context": "1.1.2", "@radix-ui/react-id": "1.1.1", "@radix-ui/react-presence": "1.1.4", "@radix-ui/react-primitive": "2.1.3", "@radix-ui/react-use-controllable-state": "1.2.2", "@radix-ui/react-use-layout-effect": "1.1.1" }, "peerDependencies": { "@types/react": "*", "@types/react-dom": "*", "react": "^16.8 || ^17.0 || ^18.0 || ^19.0 || ^19.0.0-rc", "react-dom": "^16.8 || ^17.0 || ^18.0 || ^19.0 || ^19.0.0-rc" } }, "sha512-2qrRsVGSCYasSz1RFOorXwl0H7g7J1frQtgpQgYrt+MOidtPAINHn9CPovQXb83r8ahapdx3Tu0fa/pdFFSdPg=="],

    "@radix-ui/react-collection": ["@radix-ui/react-collection@1.1.7", "", { "dependencies": { "@radix-ui/react-compose-refs": "1.1.2", "@radix-ui/react-context": "1.1.2", "@radix-ui/react-primitive": "2.1.3", "@radix-ui/react-slot": "1.2.3" }, "peerDependencies": { "@types/react": "*", "@types/react-dom": "*", "react": "^16.8 || ^17.0 || ^18.0 || ^19.0 || ^19.0.0-rc", "react-dom": "^16.8 || ^17.0 || ^18.0 || ^1

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Route, Routes, Navigate } from "react-router-dom";
import { Toaster as Sonner } from "@/components/ui/sonner";
import { Toaster } from "@/components/ui/toaster";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AuthProvider, useAuth } from "@/hooks/use-auth";
import { BusinessProvider, useBusiness } from "@/hooks/use-business";
import { AppLayout } from "@/components/AppLayout";
import Landing from "@/pages/Landing";
import Onboarding from "@/pages/Onboarding";
import Dashboard from "@/pages/Dashboard";
import Servicios from "@/pages/Servicios";
import Clientes from "@/pages/Clientes";
import Ordenes from "@/pages/Ordenes";
import NuevaOrden from "@/pages/NuevaOrden";
import Membresias from "@/pages/Membresias";
import RegistrarSocio from "@/pages/RegistrarSocio";
import ValidarPlacas from "@/pages/ValidarPlacas";
import RegistrarLavado from "@/pages/RegistrarLavado";
import NotFound from "./pages/NotFound";
import { type ReactNode } from "react";

const queryClient = new QueryClient();

function Loader() {
  return <div className="flex min-h-screen items-center justify-center"><div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent" /></div>;
}

function ProtectedRoute({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  const { hasBusiness, loading: bLoading } = useBusiness();
  if (loading || (user && bLoading)) return <Loader />;
  if (!user) return <Navigate to="/" replace />;
  if (!hasBusiness) return <Navigate to="/onboarding" replace />;
  return <>{children}</>;
}

function OnboardingRoute({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  const { hasBusiness, loading: bLoading } = useBusiness();
  if (loading || (user && bLoading)) return <Loader />;
  if (!user) return <Navigate to="/" replace />;
  if (hasBusiness) return <Navigate to="/dashboard" replace />;
  return <>{children}</>;
}

const App = () => (
  <QueryClientProvider client={queryClient}>
    <AuthProvider>
      <BusinessProvider>
        <TooltipProvider>
          <Toaster />
          <Sonner />
          <BrowserRouter>
            <Routes>
              <Route path="/" element={<Landing />} />
              <Route path="/onboarding" element={<OnboardingRoute><Onboarding /></OnboardingRoute>} />
              <Route element={<ProtectedRoute><AppLayout /></ProtectedRoute>}>
                <Route path="/dashboard" element={<Dashboard />} />
                <Route path="/servicios" element={<Servicios />} />
                <Route path="/clientes" element={<Clientes />} />
                <Route path="/ordenes" element={<Ordenes />} />
                <Route path="/ordenes/nueva" element={<NuevaOrden />} />
                <Route path="/membresias" element={<Membresias />} />
                <Route path="/socios/nuevo" element={<RegistrarSocio />} />
                <Route path="/validar" element={<ValidarPlacas />} />
                <Route path="/lavados/registrar" element={<RegistrarLavado />} />
              </Route>
              <Route path="*" element={<NotFound />} />
            </Routes>
          </BrowserRouter>
        </TooltipProvider>
      </BusinessProvider>
    </AuthProvider>
  </QueryClientProvider>
);

export default App;
