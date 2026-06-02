import asyncio
import asyncpg

DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/nommora_db"

async def update_db():
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        # Création de la table des avis réels liés aux Steals
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS reviews (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                transaction_id UUID UNIQUE REFERENCES transactions(id) ON DELETE CASCADE,
                rating INT CHECK (rating >= 1 AND rating <= 5),
                is_internal_platform BOOLEAN DEFAULT TRUE, -- TRUE = x2.0, FALSE = x0.5
                is_full_parcours BOOLEAN DEFAULT FALSE,    -- +10 points bonus
                created_at TIMESTAMP DEFAULT NOW()
            );
        """)
        
        # S'assurer que les colonnes de points existent sur le score
        await conn.execute("""
            ALTER TABLE reputation_scores 
            ADD COLUMN IF NOT EXISTS current_points INT DEFAULT 0,
            ADD COLUMN IF NOT EXISTS user_type VARCHAR(20) DEFAULT 'prestataire';
        """)
        print("✅ Matrice de confiance PostgreSQL mise à jour !")
    except Exception as e:
        print(f"❌ Erreur SQL : {e}")
    finally:
        await conn.close()

asyncio.run(update_db())
