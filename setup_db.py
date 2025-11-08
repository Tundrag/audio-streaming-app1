class DatabaseManager:
    def __init__(self):
        self.engine = create_engine(
            f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
        )
        self.Session = sessionmaker(bind=self.engine)
        self.pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

    # ... (previous methods remain the same until setup_tables) ...

    def setup_tables(self) -> bool:
        """Create all necessary tables"""
        try:
            # Define tables in order of dependency
            tables = {
                'users': """
                    CREATE TABLE IF NOT EXISTS users (
                        id SERIAL PRIMARY KEY,
                        email VARCHAR UNIQUE NOT NULL,
                        username VARCHAR UNIQUE NOT NULL,
                        password_hash VARCHAR,
                        creator_pin VARCHAR(6),
                        patreon_id VARCHAR UNIQUE,
                        patreon_tier_data JSONB,
                        role userrole NOT NULL DEFAULT 'patron',
                        is_active BOOLEAN DEFAULT true,
                        created_by INTEGER REFERENCES users(id) ON DELETE CASCADE,
                        last_login TIMESTAMP WITH TIME ZONE,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP WITH TIME ZONE,
                        stripe_customer_id VARCHAR UNIQUE
                    );
                """,
                'albums': """
                    CREATE TABLE IF NOT EXISTS albums (
                        id SERIAL PRIMARY KEY,
                        title VARCHAR NOT NULL,
                        cover_path VARCHAR,
                        created_by_id INTEGER REFERENCES users(id) ON DELETE CASCADE NOT NULL,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP WITH TIME ZONE
                    );
                """,
                'tracks': """
                    CREATE TABLE IF NOT EXISTS tracks (
                        id SERIAL PRIMARY KEY,
                        title VARCHAR NOT NULL,
                        file_path VARCHAR NOT NULL,
                        album_id INTEGER REFERENCES albums(id) ON DELETE CASCADE NOT NULL,
                        created_by_id INTEGER REFERENCES users(id) ON DELETE CASCADE NOT NULL,
                        duration INTEGER,
                        "order" INTEGER,
                        tier_requirements JSONB DEFAULT '{"minimum_cents": 0, "allowed_tier_ids": [], "is_public": true}'::jsonb,
                        last_accessed TIMESTAMP WITH TIME ZONE,
                        access_count INTEGER DEFAULT 0,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP WITH TIME ZONE
                    );
                """,
                'play_history': """
                    CREATE TABLE IF NOT EXISTS play_history (
                        id SERIAL PRIMARY KEY,
                        user_id INTEGER REFERENCES users(id) ON DELETE CASCADE NOT NULL,
                        track_id INTEGER REFERENCES tracks(id) ON DELETE CASCADE NOT NULL,
                        played_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        duration_played INTEGER,
                        completed BOOLEAN DEFAULT false
                    );
                """
            }

            with self.engine.connect() as conn:
                # Create enum type if it doesn't exist
                enum_check = """
                SELECT EXISTS (
                    SELECT 1 FROM pg_type WHERE typname = 'userrole'
                );
                """
                enum_exists = conn.execute(text(enum_check)).scalar()

                if not enum_exists:
                    conn.execute(text("""
                        CREATE TYPE UserRole AS ENUM (
                            'patreon', 'team', 'creator'
                        );
                    """))
                    logger.info("Created enum type: userrole")

                # Create tables in order
                for table_name, create_sql in tables.items():
                    if not self.table_exists(table_name):
                        conn.execute(text(create_sql))
                        logger.info(f"Created table: {table_name}")
                    else:
                        logger.info(f"Table {table_name} already exists")
                
                conn.commit()

            logger.info("All tables created successfully")
            return True

        except Exception as e:
            logger.error(f"Error creating tables: {e}")
            return False

    def create_indexes(self) -> bool:
        """Create necessary indexes"""
        try:
            indexes = [
                "CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);",
                "CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);",
                "CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);",
                "CREATE INDEX IF NOT EXISTS idx_users_creator_pin ON users(creator_pin);",
                "CREATE INDEX IF NOT EXISTS idx_tracks_album_id ON tracks(album_id);",
                "CREATE INDEX IF NOT EXISTS idx_play_history_user_id ON play_history(user_id);",
                "CREATE INDEX IF NOT EXISTS idx_play_history_track_id ON play_history(track_id);",
                "CREATE INDEX IF NOT EXISTS idx_albums_created_by_id ON albums(created_by_id);"
            ]

            with self.engine.connect() as conn:
                for index_sql in indexes:
                    conn.execute(text(index_sql))
                conn.commit()

            logger.info("Indexes created successfully")
            return True
        except Exception as e:
            logger.error(f"Error creating indexes: {e}")
            return False

    def migrate_existing_data(self) -> bool:
        """Migrate existing data to new schema"""
        try:
            with self.engine.connect() as conn:
                # Set default roles for any users without a role
                conn.execute(text("""
                    UPDATE users
                    SET role = 'patreon'::userrole
                    WHERE role IS NULL;
                """))
                
                # Generate PIN for existing creators without one
                conn.execute(text("""
                    UPDATE users
                    SET creator_pin = LPAD(FLOOR(RANDOM() * 1000000)::text, 6, '0')
                    WHERE role = 'creator' AND creator_pin IS NULL;
                """))
                
                conn.commit()
                logger.info("Data migration completed successfully")
                
                # Check if any creator exists
                creator_exists = conn.execute(text("""
                    SELECT EXISTS (
                        SELECT 1 FROM users 
                        WHERE role = 'creator'::userrole
                    );
                """)).scalar()

                if not creator_exists:
                    # Create default creator account
                    default_password = "changeme123"  # This should be changed immediately
                    default_pin = "123456"  # This should be changed immediately
                    password_hash = self.pwd_context.hash(default_password)
                    
                    conn.execute(text("""
                        INSERT INTO users (
                            email, 
                            username, 
                            password_hash,
                            creator_pin,
                            role,
                            is_active
                        ) VALUES (
                            'admin@example.com',
                            'admin',
                            :password_hash,
                            :creator_pin,
                            'creator'::userrole,
                            true
                        );
                    """), {
                        "password_hash": password_hash,
                        "creator_pin": default_pin
                    })
                    
                    conn.commit()
                    logger.info("Created initial creator account:")
                    logger.info("Email: admin@example.com")
                    logger.info("Password: changeme123")
                    logger.info("Creator PIN: 123456")
                    logger.info("PLEASE CHANGE THESE CREDENTIALS IMMEDIATELY!")
                else:
                    logger.info("Creator account already exists, skipping creation")

            return True
        except Exception as e:
            logger.error(f"Error migrating data: {e}")
            return False