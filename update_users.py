import asyncio
import asyncpg

DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/nommora_db"

async def add_columns():
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        # Ajout des deux colonnes manquantes pour le profil détaillé
        await conn.execute("""
            ALTER TABLE users 
            ADD COLUMN IF NOT EXISTS profile_picture_url TEXT, 
            ADD COLUMN IF NOT EXISTS bio TEXT;
        """)
        print("✅ Colonnes 'profile_picture_url' et 'bio' ajoutées avec succès dans PostgreSQL !")
    except Exception as e:
        print(f"❌ Erreur : {e}")
    finally:
        await conn.close()

asyncio.run(add_columns())
