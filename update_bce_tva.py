import asyncio
import asyncpg

DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/nommora_db"

async def patch_bce():
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        # Ajout des champs entreprise et points du système de notation
        await conn.execute("""
            ALTER TABLE users 
            ADD COLUMN IF NOT EXISTS user_type VARCHAR(20) DEFAULT 'prestataire', -- prestataire / magasin
            ADD COLUMN IF NOT EXISTS vat_number VARCHAR(20) UNIQUE,
            ADD COLUMN IF NOT EXISTS company_status VARCHAR(50) DEFAULT 'Active', -- Active, Faillite, En liquidation
            ADD COLUMN IF NOT EXISTS phone_number VARCHAR(30),
            ADD COLUMN IF NOT EXISTS street_address TEXT;
        """)
        
        # Modification de la table des scores pour stocker le CAPITAL POINTS de ton système
        await conn.execute("""
            ALTER TABLE reputation_scores 
            ADD COLUMN IF NOT EXISTS current_points INT DEFAULT 0;
        """)
        print("✅ Base de données Nømmora configurée pour la BCE et le système de points !")
    except Exception as e:
        print(f"❌ Erreur : {e}")
    finally:
        await conn.close()

asyncio.run(patch_bce())
