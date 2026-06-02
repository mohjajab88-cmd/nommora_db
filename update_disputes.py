import asyncio
import asyncpg

DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/nommora_db"

async def add_penalty_column():
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        # Ajout de la colonne témoin pour la pénalité de litige
        await conn.execute("""
            ALTER TABLE disputes 
            ADD COLUMN IF NOT EXISTS penalty_applied BOOLEAN DEFAULT FALSE;
        """)
        print("✅ Sécurité de pénalité activée en base avec succès !")
    except Exception as e:
        print(f"❌ Erreur : {e}")
    finally:
        await conn.close()

asyncio.run(add_penalty_column())
