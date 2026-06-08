import json
import datetime
from uuid import UUID
from typing import Optional, List, Dict
from fastapi import FastAPI, HTTPException, Depends, status, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel, EmailStr
import asyncpg
import bcrypt
from jose import jwt
import uvicorn
import cgi
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
import redis.asyncio as redis
from fastapi import Request
from fastapi.responses import RedirectResponse
import os

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/nommora_db"
else:
    if "[YOUR-PASSWORD]" in DATABASE_URL:
        DATABASE_URL = DATABASE_URL.replace("[YOUR-PASSWORD]", "METTEZ_VOTRE_VRAI_MOT_DE_PASSE_ICI")
    
    if "?" in DATABASE_URL:
        DATABASE_URL += "&prepared_statement_cache_size=0"
    else:
        DATABASE_URL += "?prepared_statement_cache_size=0"

SECRET_KEY = "NOMMORA_SUPER_SECRET_KEY_MAC_I5" 
ALGORITHM = "HS256"

# --- MOTEUR DE RÉPUTATION (UNIQUE) ---
class ReputationEngine:
    @staticmethod
    def get_status_details(score: float) -> dict:
        if score > 500:
            return {"status": "Référence", "coefficient": 2.5, "color": "0xFF00FF00"}
        elif 251 <= score <= 500:
            return {"status": "Excellent", "coefficient": 2.0, "color": "0xFF00CC00"}
        elif 101 <= score <= 250:
            return {"status": "Bon", "coefficient": 1.5, "color": "0xFF009900"}
        elif -100 <= score <= 100:
            return {"status": "Normal", "coefficient": 1.0, "color": "0xFFFFFF00"}
        elif -250 <= score <= -101:
            return {"status": "Mauvais", "coefficient": 0.1, "color": "0xFFFF0000"}
        else:  # score < -250
            return {"status": "Moyen", "coefficient": 0.5, "color": "0xFFFF6600"}

    @classmethod
    def calculate_review_impact(cls, giver_score: float, is_positive: bool, is_internal_platform: bool) -> float:
        giver_info = cls.get_status_details(giver_score)
        base_points = giver_info["coefficient"]
        direction = 1.0 if is_positive else -1.0
        points = base_points * direction
        channel_multiplier = 2.0 if is_internal_platform else 0.5
        return round(points * channel_multiplier, 2)

    @staticmethod
    def apply_transaction_bonus() -> float:
        return 10.0

    @staticmethod
    def apply_dispute_penalty() -> float:
        return -2.0

    @staticmethod
    def process_pardon() -> dict:
        return {"new_score": 0.0, "has_pardon_badge": True, "status": "Normal"}

class TranslateRequest(BaseModel):
    text: str
    target_lang: str = "fr"

app = FastAPI()
# Rate limiter
limiter = Limiter(key_func=get_remote_address, default_limits=["100/minute"])
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Redis (cache)
redis_client = redis.from_url("redis://localhost:6379", decode_responses=True)
app.state.redis = redis_client
# --- CORS (corrigé : une seule fois) ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

db_pool = None
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/login")

# --- MODÈLES DE DONNÉES ---
class UserRegister(BaseModel):
    email: EmailStr
    password: str
    full_name: str
    city: str
    user_type: str = "prestataire"
    vat_number: Optional[str] = None
    phone_number: Optional[str] = None
    street_address: Optional[str] = None

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class StealCreate(BaseModel):
    provider_id: UUID
    amount: float

class DisputeCreate(BaseModel):
    transaction_id: UUID
    reason: str

class DisputeResolve(BaseModel):
    transaction_id: UUID

class EnquetePardon(BaseModel):
    user_id: UUID
    inspecteur_note: str

class ReviewCreate(BaseModel):
    transaction_id: UUID
    rating: int
    is_internal_platform: bool = True
    is_full_parcours: bool = False

class CompanyStatusForce(BaseModel):
    vat_number: str
    new_status: str

class HookupAction(BaseModel):
    room_id: str
    offer_amount: float

class StatusCheckRequest(BaseModel):
    user_id: UUID

class ReleaseFundsRequest(BaseModel):
    transaction_id: int
    provider_id: UUID
    total_amount: float

class SubmitProofRequest(BaseModel):
    user_id: str
    document_type: str
    document_url: str

class VerifyUserAdminRequest(BaseModel):
    user_id: str
    approve_identity: bool
    approve_company: bool

# --- DÉPENDANCE : VÉRIFICATION DU TOKEN JWT ---
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/login")

async def get_current_user(token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception
        return email
    except JWTError:
        raise credentials_exception
    
# --- GESTION DE LA CONNEXION DB ---
@app.on_event("startup")
async def startup():
    global db_pool
    try:
        db_pool = await asyncpg.create_pool(DATABASE_URL)
        print("✅ Serveur Nømmora connecté à PostgreSQL")
    except Exception as e:
        print(f"❌ Erreur de connexion DB : {e}")

@app.on_event("shutdown")
async def shutdown():
    if db_pool:
        await db_pool.close()

# ===========================
# ENDPOINTS AUTHENTIFICATION
# ===========================
@app.post("/register")
async def register(user: UserRegister):
    hashed = bcrypt.hashpw(user.password.encode(), bcrypt.gensalt()).decode()
    async with db_pool.acquire() as conn:
        try:
            res = await conn.fetchrow(
                "INSERT INTO users (email, password_hash, full_name, city, user_type, vat_number, phone_number, street_address) VALUES ($1, $2, $3, $4, $5, $6, $7, $8) RETURNING id",
                user.email, hashed, user.full_name, user.city, user.user_type, user.vat_number, user.phone_number, user.street_address
            )
            await conn.execute("INSERT INTO reputation_scores (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING", res['id'])
            return {"message": f"Utilisateur {user.full_name} créé avec succès !"}
        except asyncpg.UniqueViolationError:
            raise HTTPException(status_code=400, detail="Cet email ou numéro de TVA est déjà utilisé.")

@app.post("/login")
@limiter.limit("5/minute")
async def login(request: Request, user: UserLogin):
    async with db_pool.acquire() as conn:
        record = await conn.fetchrow("SELECT id, email, password_hash FROM users WHERE email = $1", user.email)
        if not record or not bcrypt.checkpw(user.password.encode(), record['password_hash'].encode()):
            raise HTTPException(status_code=401, detail="Identifiants incorrects.")
        
        expire = datetime.datetime.utcnow() + datetime.timedelta(days=30)
        token = jwt.encode({"sub": record['email'], "user_id": str(record['id']), "exp": expire}, SECRET_KEY, algorithm=ALGORITHM)
        return {"access_token": token, "token_type": "bearer"}
# ===========================
# TRADUCTION (NOUVEAU)
# ===========================

from deep_translator import GoogleTranslator

# Initialisation (une seule fois, en dehors de la fonction)
translator = GoogleTranslator(source='auto', target='fr')

@app.post("/translate")
async def translate_text(request: TranslateRequest):
    try:
        # deep-translator utilise la méthode 'translate' directement
        translated = translator.translate(request.text)
        return {
            "original": request.text,
            "translated": translated,
            "target_lang": request.target_lang,
            "source_lang": "auto"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur de traduction : {str(e)}")# ===========================

# RECHERCHE, STEALS, LITIGES, AVIS...
# ===========================
@app.get("/search")
async def search(city: Optional[str] = None, vat_number: Optional[str] = None):
    async with db_pool.acquire() as conn:
        if vat_number or (city and city.startswith("BE")):
            target_vat = vat_number if vat_number else city
            query = """
                SELECT u.id, u.full_name, u.city, u.vat_number, u.company_status, u.phone_number, u.street_address,
                       COALESCE(s.current_points, 0) as current_points, COALESCE(s.status, 'Normal') as status
                FROM users u
                LEFT JOIN reputation_scores s ON u.id = s.user_id
                WHERE u.vat_number = $1
            """
            row = await conn.fetchrow(query, target_vat.strip())
            if row:
                return [dict(row)]
            else:
                return [{
                    "id": "00000000-0000-0000-0000-000000000000",
                    "full_name": "Société Détectée via BCE (Non inscrite)",
                    "vat_number": target_vat,
                    "company_status": "Active (Info BCE officielle)",
                    "city": "Bruxelles",
                    "phone_number": "Non renseigné sur la plateforme",
                    "street_address": "Rue de la Loi, 1000 Bruxelles",
                    "current_points": 0,
                    "status": "Inconnu"
                }]

        query = """
            SELECT u.id, u.full_name, u.city, u.vat_number, u.company_status, u.phone_number, u.street_address,
                   COALESCE(s.current_points, 0) as current_points, COALESCE(s.status, 'Normal') as status
            FROM users u
            LEFT JOIN reputation_scores s ON u.id = s.user_id
            WHERE u.city ILIKE $1
            ORDER BY s.current_points DESC
        """
        rows = await conn.fetch(query, f"%{city}%")
        return [dict(r) for r in rows]

@app.get("/providers/{provider_id}")
async def get_provider(provider_id: UUID):
    # 1. Tenter de lire depuis Redis
    cache_key = f"provider:{provider_id}"
    cached = await app.state.redis.get(cache_key)
    if cached:
        # Retourner les données depuis le cache
        return json.loads(cached)

    # 2. Sinon, interroger la base de données
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT u.full_name, u.city, u.profile_picture_url, u.bio, u.user_type,
                   u.vat_number, u.phone_number, u.street_address,
                   COALESCE(s.current_points, 0) as current_points,
                   COALESCE(s.status, 'Normal') as status
            FROM users u
            LEFT JOIN reputation_scores s ON u.id = s.user_id
            WHERE u.id = $1
        """, provider_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Prestataire non trouvé.")
        data = dict(row)

        # 3. Stocker dans Redis pour 5 minutes (300 secondes)
        await app.state.redis.setex(cache_key, 300, json.dumps(data))

        return data
async def update_user_reputation(user_id: UUID):
    async with db_pool.acquire() as conn:
        stats = await conn.fetchrow("""
            SELECT COUNT(t.id) as total_tx 
            FROM transactions t 
            WHERE t.provider_id = $1 AND t.status = 'completed'
        """, user_id)
        total_tx = int(stats['total_tx']) if stats and stats['total_tx'] else 0
        final_score = round(min(5.0 + (total_tx * 0.1), 10.0), 2)
        status_label = "Normal"
        if final_score >= 8.5: status_label = "Excellent"
        elif final_score < 4.0: status_label = "Mauvais"
        await conn.execute("""
            INSERT INTO reputation_scores (user_id, current_score, status, total_transactions)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (user_id) DO UPDATE SET current_score = $2, status = $3, total_transactions = $4, updated_at = NOW()
        """, user_id, final_score, status_label, total_tx)

@app.post("/steals")
async def create_steal(tx: StealCreate, current_user_email: str = Depends(get_current_user)):
    async with db_pool.acquire() as conn:
        client = await conn.fetchrow("SELECT id FROM users WHERE email = $1", current_user_email)
        if not client:
            raise HTTPException(status_code=404, detail="Client introuvable.")
        tx_id = await conn.fetchval("""
            INSERT INTO transactions (client_id, provider_id, amount, status)
            VALUES ($1, $2, $3, 'pending')
            RETURNING id
        """, client['id'], tx.provider_id, tx.amount)
        return {"steal_id": str(tx_id), "status": "pending", "amount": tx.amount}

@app.post("/steals/review")
async def review_steal(data: ReviewCreate):
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            tx = await conn.fetchrow("SELECT client_id, provider_id, status FROM transactions WHERE id = $1", data.transaction_id)
            if not tx:
                raise HTTPException(status_code=404, detail="L'affaire (Steal) n'existe pas.")
            if tx['status'] != 'completed':
                raise HTTPException(status_code=400, detail="Impossible de noter un contrat non validé.")
            client_profile = await conn.fetchrow("SELECT u.user_type, COALESCE(s.current_points, 0) as points FROM users u LEFT JOIN reputation_scores s ON u.id = s.user_id WHERE u.id = $1", tx['client_id'])
            provider_profile = await conn.fetchrow("SELECT u.user_type, COALESCE(s.current_points, 0) as points FROM users u LEFT JOIN reputation_scores s ON u.id = s.user_id WHERE u.id = $1", tx['provider_id'])

            def get_coeff(user_type: str, points: int) -> float:
                if user_type == "magasin":
                    if points >= 5000: return 2.5
                    if points >= 2501: return 2.0
                    if points >= 1001: return 1.5
                    if points <= -1001: return 0.1
                    if points <= 0: return 0.5
                    return 1.0
                else:
                    if points >= 500: return 2.5
                    if points >= 251: return 2.0
                    if points >= 101: return 1.5
                    if points <= -101: return 0.1
                    if points <= 0: return 0.5
                    return 1.0

            coeff_evaluateur = get_coeff(client_profile['user_type'], client_profile['points'])
            coeff_evalue = get_coeff(provider_profile['user_type'], provider_profile['points'])
            platform_multiplier = 2.0 if data.is_internal_platform else 0.5
            note_de_base = float(data.rating)
            impact_points = round(note_de_base * coeff_evaluateur * coeff_evalue * platform_multiplier)
            if data.is_full_parcours:
                impact_points += 10

            await conn.execute("""
                INSERT INTO reviews (transaction_id, rating, is_internal_platform, is_full_parcours)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (transaction_id) DO NOTHING
            """, data.transaction_id, data.rating, data.is_internal_platform, data.is_full_parcours)

            await conn.execute("UPDATE reputation_scores SET current_points = current_points + $1, updated_at = NOW() WHERE user_id = $2", impact_points, tx['provider_id'])
            nouveau_solde = await conn.fetchval("SELECT current_points FROM reputation_scores WHERE user_id = $1", tx['provider_id'])
            p_type = provider_profile['user_type']
            if p_type == "magasin":
                if nouveau_solde >= 5000: status_label = "Référence"
                elif nouveau_solde >= 2501: status_label = "Excellent"
                elif nouveau_solde >= 1001: status_label = "Bon"
                elif nouveau_solde <= -1001: status_label = "Mauvais"
                elif nouveau_solde <= 0: status_label = "Moyen"
                else: status_label = "Normal"
            else:
                if nouveau_solde >= 500: status_label = "Référence"
                elif nouveau_solde >= 251: status_label = "Excellent"
                elif nouveau_solde >= 101: status_label = "Bon"
                elif nouveau_solde <= -101: status_label = "Mauvais"
                elif nouveau_solde <= 0: status_label = "Moyen"
                else: status_label = "Normal"

            await conn.execute("UPDATE reputation_scores SET status = $2 WHERE user_id = $1", tx['provider_id'], status_label)
            return {
                "message": "Avis certifié.",
                "points_transmis": impact_points,
                "nouveau_solde_prestataire": nouveau_solde,
                "statut_obtenu": status_label
            }

@app.post("/steals/release")
async def release_steal(data: DisputeResolve):
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            tx = await conn.fetchrow("SELECT provider_id, status FROM transactions WHERE id = $1", data.transaction_id)
            if not tx:
                raise HTTPException(status_code=404, detail="Contrat introuvable.")
            if tx['status'] == 'completed':
                raise HTTPException(status_code=400, detail="Ce contrat est déjà validé.")
            await conn.execute("UPDATE transactions SET status = 'completed' WHERE id = $1", data.transaction_id)
            provider_id = tx['provider_id']
            if provider_id:
                await update_user_reputation(provider_id)
        return {"message": "Fonds débloqués ! Réputation du prestataire augmentée."}

@app.post("/disputes")
async def create_dispute(data: DisputeCreate):
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            tx = await conn.fetchrow("SELECT provider_id, status, created_at FROM transactions WHERE id = $1", data.transaction_id)
            if not tx:
                raise HTTPException(status_code=404, detail="Contrat introuvable.")
            if tx['status'] == 'completed':
                date_limite = tx['created_at'] + datetime.timedelta(days=5)
                if datetime.datetime.utcnow() > date_limite:
                    raise HTTPException(status_code=400, detail="Délai de réclamation expiré (Maximum 5 jours après validation pour protéger le prestataire).")
            await conn.execute("INSERT INTO disputes (transaction_id, reason, penalty_applied) VALUES ($1, $2, TRUE)", data.transaction_id, data.reason)
            provider_id = tx['provider_id']
            if provider_id:
                await conn.execute("UPDATE reputation_scores SET current_score = GREATEST(0.0, current_score - 2.0) WHERE user_id = $1", provider_id)
        return {"message": "Litige ouvert. Malus appliqué."}

@app.post("/disputes/resolve")
async def resolve_dispute(data: DisputeResolve):
    async with db_pool.acquire() as conn:
        async with conn.transaction():
            dispute_exists = await conn.fetchval("SELECT penalty_applied FROM disputes WHERE transaction_id = $1", data.transaction_id)
            if not dispute_exists:
                raise HTTPException(status_code=400, detail="Aucun litige actif.")
            provider_id = await conn.fetchval("SELECT provider_id FROM transactions WHERE id = $1", data.transaction_id)
            if provider_id:
                await conn.execute("UPDATE reputation_scores SET current_score = LEAST(10.0, current_score + 2.0) WHERE user_id = $1", provider_id)
                await conn.execute("DELETE FROM disputes WHERE transaction_id = $1", data.transaction_id)
        return {"message": "Litige résolu avec succès !"}

@app.post("/bce/simulate-status")
async def simulate_bce_status(data: CompanyStatusForce):
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE users SET company_status = $1 WHERE vat_number = $2", data.new_status, data.vat_number.strip())
        if data.new_status == "Faillite":
            user_id = await conn.fetchval("SELECT id FROM users WHERE vat_number = $1", data.vat_number.strip())
            if user_id:
                await conn.execute("UPDATE reputation_scores SET current_points = -250, status = 'Mauvais', updated_at = NOW() WHERE user_id = $1", user_id)
        return {"message": f"Statut BCE mis à jour en : {data.new_status}"}

@app.get("/users/{user_id}/transactions")
async def get_user_transactions(user_id: UUID, current_user_email: str = Depends(get_current_user)):
    async with db_pool.acquire() as conn:
        auth_user = await conn.fetchrow("SELECT id FROM users WHERE email = $1", current_user_email)
        if not auth_user or auth_user['id'] != user_id:
            raise HTTPException(status_code=403, detail="Accès interdit. Vous ne pouvez consulter que l'intimité de vos propres affaires.")
        rows = await conn.fetch("""
            SELECT t.id, t.amount, t.status, t.created_at,
                   u_client.full_name as client_name,
                   u_provider.full_name as provider_name
            FROM transactions t
            JOIN users u_client ON t.client_id = u_client.id
            JOIN users u_provider ON t.provider_id = u_provider.id
            WHERE t.client_id = $1 OR t.provider_id = $1
            ORDER BY t.created_at DESC
        """, user_id)
        return [dict(r) for r in rows]

@app.get("/world/stats")
async def get_world_statistics(current_user_email: str = Depends(get_current_user)):
    async with db_pool.acquire() as conn:
        total_released = await conn.fetchval("SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE status = 'released'")
        total_secured = await conn.fetchval("SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE status = 'pending'")
        total_steals = await conn.fetchval("SELECT COUNT(*) FROM transactions")
        total_users = await conn.fetchval("SELECT COUNT(*) FROM users")
        return {
            "total_released_funds": float(total_released),
            "total_secured_funds": float(total_secured),
            "total_steals_count": int(total_steals),
            "total_users_count": int(total_users)
        }

@app.post("/hookup/submit")
async def submit_hookup_offer(data: HookupAction, current_user_email: str = Depends(get_current_user)):
    async with db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT id FROM users WHERE email = $1", current_user_email)
        if not user:
            raise HTTPException(status_code=404, detail="Utilisateur non trouvé.")
        room = await conn.fetchrow("SELECT status, current_offer FROM hookup_rooms WHERE room_id = $1", data.room_id)
        if not room:
            prestataire_id = UUID("511481ef-824f-43c7-95e5-7ac69fc65134")
            await conn.execute("INSERT INTO hookup_rooms (room_id, client_id, provider_id, current_offer, status) VALUES ($1, $2, $3, $4, 'negotiating')", data.room_id, user['id'], prestataire_id, data.offer_amount)
            return {"status": "success", "message": f"Salon {data.room_id} matérialisé. Offre initiale à {data.offer_amount} €."}
        if room["status"] != "negotiating":
            raise HTTPException(status_code=400, detail="Négociation scellée. Impossible de modifier le montant.")
        await conn.execute("UPDATE hookup_rooms SET current_offer = $1, updated_at = CURRENT_TIMESTAMP WHERE room_id = $2", data.offer_amount, data.room_id)
        return {"status": "success", "message": f"Nouvelle proposition à {data.offer_amount} € transmise aux partenaires."}

@app.post("/hookup/lock")
async def lock_and_convert_to_steal(data: HookupAction, current_user_email: str = Depends(get_current_user)):
    import uuid
    async with db_pool.acquire() as conn:
        room = await conn.fetchrow("SELECT client_id, provider_id, current_offer, status FROM hookup_rooms WHERE room_id = $1", data.room_id)
        if not room:
            raise HTTPException(status_code=444, detail="Salon Hookup introuvable pour le scellage.")
        if room["status"] != "negotiating":
            raise HTTPException(status_code=400, detail="Ce pacte est déjà verrouillé ou converti.")
        await conn.execute("UPDATE hookup_rooms SET status = 'locked', updated_at = CURRENT_TIMESTAMP WHERE room_id = $1", data.room_id)
        new_steal_uuid = uuid.uuid4()
        await conn.execute("INSERT INTO transactions (id, client_id, provider_id, amount, status) VALUES ($1, $2, $3, $4, 'pending')", new_steal_uuid, room["client_id"], room["provider_id"], room["current_offer"])
        return {"status": "success", "message": f"Négociation scellée au montant de {room['current_offer']} €.", "steal_id": str(new_steal_uuid)}

# ===========================
# COMMISSION ENGINE (UNIQUE)
# ===========================
class CommissionEngine:
    NOMMORA_FEES_PERCENT = 0.03
    TVA_AND_TAXES_PERCENT = 0.21
    INFRASTRUCTURE_COSTS = 0.05
    CONNECTOR_SHARE_PERCENT = 0.20

    @classmethod
    def split_transaction_funds(cls, total_amount: float) -> dict:
        nommora_gross_fees = round(total_amount * cls.NOMMORA_FEES_PERCENT, 2)
        provider_funds = round(total_amount - nommora_gross_fees, 2)
        tva_deducted = round(nommora_gross_fees * cls.TVA_AND_TAXES_PERCENT, 2)
        infra_deducted = round(nommora_gross_fees * cls.INFRASTRUCTURE_COSTS, 2)
        net_profit_before_commission = round(nommora_gross_fees - tva_deducted - infra_deducted, 2)
        if net_profit_before_commission < 0: net_profit_before_commission = 0.0
        connector_commission = round(net_profit_before_commission * cls.CONNECTOR_SHARE_PERCENT, 2)
        nommora_pure_net = round(net_profit_before_commission - connector_commission, 2)
        return {
            "total_transaction": total_amount,
            "provider_receives": provider_funds,
            "nommora_gross": nommora_gross_fees,
            "taxes_and_tva": tva_deducted,
            "infra_costs": infra_deducted,
            "net_profit_before_commission": net_profit_before_commission,
            "connector_receives": connector_commission,
            "nommora_pure_net": nommora_pure_net
        }

@app.post("/steals/release_funds")
async def release_and_distribute_commissions(request: ReleaseFundsRequest):
    import psycopg2
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        split = CommissionEngine.split_transaction_funds(request.total_amount)
        cur.execute("SELECT connector_id FROM users WHERE id = %s", (request.provider_id,))
        res = cur.fetchone()
        connector_id = res[0] if res else None
        if connector_id and split["connector_receives"] > 0:
            cur.execute("UPDATE users SET wallet_balance = wallet_balance + %s WHERE id = %s", (split["connector_receives"], connector_id))
        cur.execute("UPDATE users SET wallet_balance = wallet_balance + %s WHERE id = %s", (split["provider_receives"], request.provider_id))
        conn.commit()
        return {"status": "SUCCESS_TAXED_AND_DISTRIBUTED", "breakdown": split, "has_active_connector": connector_id is not None}
    except Exception as e:
        if 'conn' in locals(): conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if 'cur' in locals(): cur.close()
        if 'conn' in locals(): conn.close()

# ===========================
# KYC, SYNCHRO, REDIRECTS...
# ===========================
@app.post("/users/kyc/submit")
def submit_verification_proof(request: SubmitProofRequest):
    import psycopg2
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        if request.document_type in ['IDENTITY_CARD', 'PASSPORT']:
            query = "UPDATE users SET identity_status = 'UNDER_REVIEW' WHERE id = %s RETURNING id"
        elif request.document_type == 'BCE_DOCUMENT':
            query = "UPDATE users SET company_proof_status = 'UNDER_REVIEW' WHERE id = %s RETURNING id"
        else:
            raise HTTPException(status_code=400, detail="Type de document invalide")
        cur.execute(query, (request.user_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Utilisateur introuvable")
        conn.commit()
        return {"status": "PROOF_SUBMITTED", "message": f"Document {request.document_type} reçu et mis en attente de vérification."}
    except Exception as e:
        if 'conn' in locals(): conn.rollback()
        if isinstance(e, HTTPException): raise e
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if 'cur' in locals(): cur.close()
        if 'conn' in locals(): conn.close()

@app.post("/admin/kyc/verify")
def admin_verify_user(request: VerifyUserAdminRequest):
    import psycopg2
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        id_status = "VERIFIED" if request.approve_identity else "REJECTED"
        co_status = "VERIFIED" if request.approve_company else "REJECTED"
        final_global_verification = request.approve_identity and request.approve_company
        cur.execute("UPDATE users SET identity_status = %s, company_proof_status = %s, is_verified = %s WHERE id = %s RETURNING id, is_verified", (id_status, co_status, final_global_verification, request.user_id))
        result = cur.fetchone()
        if not result:
            raise HTTPException(status_code=404, detail="Utilisateur introuvable")
        conn.commit()
        return {
            "status": "KYC_PROCESS_COMPLETED",
            "user_id": request.user_id,
            "identity_status": id_status,
            "company_status": co_status,
            "is_account_fully_verified": final_global_verification
        }
    except Exception as e:
        if 'conn' in locals(): conn.rollback()
        if isinstance(e, HTTPException): raise e
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if 'cur' in locals(): cur.close()
        if 'conn' in locals(): conn.close()

@app.post("/users/reputation/sync-status")
def sync_user_reputation_status(request: StatusCheckRequest):
    import psycopg2
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute("SELECT score, has_pardon_badge FROM users WHERE id = %s", (request.user_id,))
        user_data = cur.fetchone()
        if not user_data:
            raise HTTPException(status_code=404, detail="Utilisateur introuvable dans le réseau")
        current_score = float(user_data[0]) if user_data[0] is not None else 0.0
        has_pardon = bool(user_data[1])
        metrics = ReputationEngine.get_status_details(current_score)
        return {
            "status": "SYNCHRONIZED",
            "user_id": request.user_id,
            "score": current_score,
            "reputation_status": metrics["status"],
            "coefficient_sérieux": metrics["coefficient"],
            "has_pardon_badge": has_pardon
        }
    except Exception as e:
        if isinstance(e, HTTPException): raise e
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if 'cur' in locals(): cur.close()
        if 'conn' in locals(): conn.close()

@app.get("/.well-known/apple-app-site-association")
async def apple_app_site_association():
    return {"appclips": {"apps": ["YOUR_TEAM_ID.com.nommora.mobile.Clip"]}}

@app.get("/.well-known/assetlinks.json")
async def google_assetlinks_association():
    return [{"relation": ["delegate_permission/common.handle_all_urls"], "target": {"namespace": "android_app", "package_name": "com.nommora.mobile.instant", "sha256_cert_fingerprints": ["YOUR_ANDROID_SIGNING_SHA256"]}}]

@app.get("/gate")
async def moon_gate_redirect(user_agent: Optional[str] = Header(None)):
    import user_agents
    if not user_agent:
        return RedirectResponse(url="https://nommora.com")
    ua = user_agents.parse(user_agent)
    if ua.is_mobile and "iPhone" in user_agent:
        return RedirectResponse(url="https://apple.com")
    elif ua.is_mobile and "Android" in user_agent:
        return RedirectResponse(url="market://details?id=com.nommora.app")
    else:
        return RedirectResponse(url="https://nommora.com")

@app.get("/search/v2")
def unified_merit_search(city: str, requesting_user_id: int):
    import psycopg2
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute("SELECT region_code FROM users WHERE id = %s", (requesting_user_id,))
        requester = cur.fetchone()
        requester_region = requester[0] if requester else "Unknown"
        cur.execute("SELECT id, username, score, has_premium_subscription, region_code FROM users WHERE (has_premium_subscription = true) OR (has_premium_subscription = false AND region_code = %s) ORDER BY score DESC", (requester_region,))
        records = cur.fetchall()
        results = [{"user_id": row[0], "username": row[1], "score": float(row[2]), "is_premium": row[3], "region": row[4], "visibility_scope": "GLOBAL" if row[3] else "REGIONAL"} for row in records]
        return {"status": "SUCCESS", "search_city_target": city, "total_matches_found": len(results), "prestataires": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if 'cur' in locals(): cur.close()
        if 'conn' in locals(): conn.close()

@app.get("/health")
async def health_check():
    return {"status": "ok"}

if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
