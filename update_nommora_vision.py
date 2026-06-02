import asyncio
import asyncpg

DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/nommora_db"

async def patch_database():
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute("""
            ALTER TABLE users 
            ADD COLUMN IF NOT EXISTS is_eudi_verified BOOLEAN DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS company_register_id TEXT;
        """)
        await conn.execute("""
            ALTER TABLE reputation_scores 
            ADD COLUMN IF NOT EXISTS has_pardon_badge BOOLEAN DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS total_pardons_granted INT DEFAULT 0;
        """)
        print("✅ Base de données Nømmora mise aux normes de la vision Åø !")
    except Exception as e:
        print(f"❌ Erreur : {e}")
    finally:
        await conn.close()

asyncio.run(patch_database())
