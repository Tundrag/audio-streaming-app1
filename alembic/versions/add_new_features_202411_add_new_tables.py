"""Add new features and tables

Revision ID: add_new_features_202411
Revises: None
Create Date: 2024-03-06
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from datetime import datetime

# revision identifiers, used by Alembic.
revision = 'add_new_features_202411'
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    connection = op.get_bind()
    
    # First, safely drop existing enums if they exist
    op.execute('DROP TYPE IF EXISTS notificationtype CASCADE;')
    op.execute('DROP TYPE IF EXISTS auditlogtype CASCADE;')
    op.execute('DROP TYPE IF EXISTS contentvisibility CASCADE;')

    # Create enums
    op.execute("""
        DO $$ 
        BEGIN
            CREATE TYPE notificationtype AS ENUM (
                'comment', 'reply', 'mention', 'new_content', 'tier_update', 'system'
            );
            
            CREATE TYPE auditlogtype AS ENUM (
                'create', 'update', 'delete', 'login', 'permission_change', 'content_access'
            );
            
            CREATE TYPE contentvisibility AS ENUM (
                'public', 'private', 'tier_only', 'scheduled'
            );
        EXCEPTION 
            WHEN duplicate_object THEN NULL;
        END $$;
    """)
    
    # Add new columns to users table with default values for nullable columns
    op.add_column('users', sa.Column('uuid', sa.String(), nullable=True))
    op.add_column('users', sa.Column('patreon_refresh_token', sa.String(), nullable=True))
    op.add_column('users', sa.Column('patreon_access_token', sa.String(), nullable=True))
    op.add_column('users', sa.Column('patreon_token_expires_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('users', sa.Column('avatar_url', sa.String(), nullable=True))
    op.add_column('users', sa.Column('bio', sa.Text(), nullable=True))
    op.add_column('users', sa.Column('social_links', postgresql.JSONB(), server_default='{}', nullable=False))
    op.add_column('users', sa.Column('preferences', postgresql.JSONB(), server_default='{}', nullable=False))
    op.add_column('users', sa.Column('notification_settings', postgresql.JSONB(), server_default='{}', nullable=False))
    op.add_column('users', sa.Column('stripe_subscription_id', sa.String(), nullable=True))
    op.add_column('users', sa.Column('email_verified', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('users', sa.Column('email_verification_token', sa.String(), nullable=True))
    op.add_column('users', sa.Column('password_reset_token', sa.String(), nullable=True))
    op.add_column('users', sa.Column('password_reset_expires', sa.DateTime(timezone=True), nullable=True))
    op.add_column('users', sa.Column('failed_login_attempts', sa.Integer(), server_default='0', nullable=False))
    op.add_column('users', sa.Column('last_failed_login', sa.DateTime(timezone=True), nullable=True))
    op.add_column('users', sa.Column('account_locked_until', sa.DateTime(timezone=True), nullable=True))
    op.add_column('users', sa.Column('timezone', sa.String(), server_default='UTC', nullable=False))
    op.add_column('users', sa.Column('language_preference', sa.String(), server_default='en', nullable=False))

    # Generate UUIDs for existing users
    op.execute("""
        UPDATE users 
        SET uuid = gen_random_uuid()::text 
        WHERE uuid IS NULL;
    """)
    
    # Make uuid not nullable after populating
    op.alter_column('users', 'uuid',
                    existing_type=sa.String(),
                    nullable=False)

    # Create campaign_tiers table
    op.create_table('campaign_tiers',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('uuid', sa.String(), nullable=False),
        sa.Column('patreon_tier_id', sa.String(), nullable=False),
        sa.Column('creator_id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('amount_cents', sa.Integer(), nullable=False),
        sa.Column('patron_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('benefits', postgresql.JSONB(), server_default='{}', nullable=False),
        sa.Column('downloads_allowed', sa.Integer(), server_default='0', nullable=False),
        sa.Column('custom_perks', postgresql.JSONB(), server_default='{}', nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('position', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['creator_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('creator_id', 'title', name='uq_creator_tier_title'),
        sa.UniqueConstraint('patreon_tier_id'),
        sa.UniqueConstraint('uuid')
    )
    op.create_index('ix_campaign_tiers_creator_id', 'campaign_tiers', ['creator_id'])

    # Create user_sessions table
    op.create_table('user_sessions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('uuid', sa.String(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('session_id', sa.String(), nullable=False),
        sa.Column('ip_address', sa.String(), nullable=True),
        sa.Column('user_agent', sa.String(), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('last_active', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('session_id'),
        sa.UniqueConstraint('uuid')
    )
    op.create_index('ix_user_sessions_user_id', 'user_sessions', ['user_id'])

    # Create playback_progress table
    op.create_table('playback_progress',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('uuid', sa.String(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('track_id', sa.Integer(), nullable=False),
        sa.Column('position', sa.Integer(), server_default='0', nullable=False),
        sa.Column('duration', sa.Integer(), server_default='0', nullable=False),
        sa.Column('completed', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('play_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('last_played', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('completion_rate', sa.Float(), server_default='0.0', nullable=False),
        sa.Column('device_info', postgresql.JSONB(), server_default='{}', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['track_id'], ['tracks.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'track_id', name='uq_user_track_progress'),
        sa.UniqueConstraint('uuid')
    )
    op.create_index('ix_playback_progress_track_id', 'playback_progress', ['track_id'])
    op.create_index('ix_playback_progress_user_id', 'playback_progress', ['user_id'])

    # Create comments table
    op.create_table('comments',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('uuid', sa.String(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('track_id', sa.Integer(), nullable=True),
        sa.Column('album_id', sa.Integer(), nullable=True),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('timestamp', sa.Integer(), nullable=True),
        sa.Column('parent_id', sa.Integer(), nullable=True),
        sa.Column('is_edited', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('is_hidden', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('edit_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('last_edited_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('edited_by_id', sa.Integer(), nullable=True),
        sa.Column('moderation_status', sa.String(), server_default='approved', nullable=False),
        sa.Column('moderation_reason', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint('track_id IS NOT NULL OR album_id IS NOT NULL', name='check_comment_target'),
        sa.ForeignKeyConstraint(['album_id'], ['albums.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['edited_by_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['parent_id'], ['comments.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['track_id'], ['tracks.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('uuid')
    )
    op.create_index('ix_comments_album_id', 'comments', ['album_id'])
    op.create_index('ix_comments_track_id', 'comments', ['track_id'])
    op.create_index('ix_comments_user_id', 'comments', ['user_id'])

    # Create comment_likes table
    op.create_table('comment_likes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('uuid', sa.String(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('comment_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['comment_id'], ['comments.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'comment_id', name='uq_user_comment_like'),
        sa.UniqueConstraint('uuid')
    )

    # Create notifications table
    op.create_table('notifications',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('uuid', sa.String(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('type', sa.String(50), nullable=False),  # Start with string type
        sa.Column('title', sa.String(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('is_read', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('data', postgresql.JSONB(), server_default='{}', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('uuid')
    )
    op.create_index('ix_notifications_user_id', 'notifications', ['user_id'])

    # Add check constraint for notification type
    op.execute("""
        ALTER TABLE notifications
        ADD CONSTRAINT check_notification_type
        CHECK (type IN ('comment', 'reply', 'mention', 'new_content', 'tier_update', 'system'));
    """)

    # Convert notification type to enum
    op.execute("""
        ALTER TABLE notifications 
        ALTER COLUMN type TYPE notificationtype 
        USING type::notificationtype;
    """)

def downgrade():
    # Drop tables in correct order (reverse of creation)
    op.drop_table('notifications')
    op.drop_table('comment_likes')
    op.drop_table('comments')
    op.drop_table('playback_progress')
    op.drop_table('user_sessions')
    op.drop_table('campaign_tiers')

    # Remove user columns
    columns_to_drop = [
        'uuid', 'patreon_refresh_token', 'patreon_access_token', 
        'patreon_token_expires_at', 'avatar_url', 'bio', 'social_links',
        'preferences', 'notification_settings', 'stripe_subscription_id',
        'email_verified', 'email_verification_token', 'password_reset_token',
        'password_reset_expires', 'failed_login_attempts', 'last_failed_login',
        'account_locked_until', 'timezone', 'language_preference'
    ]

    for column in columns_to_drop:
        with op.batch_alter_table('users') as batch_op:
            batch_op.drop_column(column)

    # Drop enums last
    op.execute('DROP TYPE IF EXISTS notificationtype CASCADE;')
    op.execute('DROP TYPE IF EXISTS auditlogtype CASCADE;')
    op.execute('DROP TYPE IF EXISTS contentvisibility CASCADE;')