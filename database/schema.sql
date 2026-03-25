-- Premium Dating TWA - PostgreSQL Database Schema
-- Complete schema for user management, matching, and chat functionality

-- Enable PostGIS for geolocation features (optional but recommended)
CREATE EXTENSION IF NOT EXISTS postgis;

-- Users table: Core user profiles
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    telegram_id BIGINT UNIQUE NOT NULL,
    phone VARCHAR(20) UNIQUE NOT NULL,
    country_code VARCHAR(5),
    is_blocked BOOLEAN DEFAULT FALSE,
    
    -- Profile information
    username VARCHAR(255),
    first_name VARCHAR(255),
    last_name VARCHAR(255),
    bio TEXT,
    gender VARCHAR(10) CHECK (gender IN ('male', 'female', 'other')),
    birthdate DATE,
    
    -- Location (using PostGIS geography type for accurate distance calculations)
    location GEOGRAPHY(POINT, 4326),
    city VARCHAR(255),
    country VARCHAR(255),
    
    -- Preferences
    looking_for VARCHAR(10) CHECK (looking_for IN ('male', 'female', 'everyone')),
    age_min INTEGER DEFAULT 18,
    age_max INTEGER DEFAULT 99,
    max_distance INTEGER DEFAULT 50, -- in kilometers
    
    -- Photos (array of Cloudinary/S3 URLs)
    photos_urls TEXT[] DEFAULT '{}',
    primary_photo_url TEXT,
    
    -- Interests/Tags
    interests TEXT[] DEFAULT '{}',
    
    -- Account status
    is_active BOOLEAN DEFAULT TRUE,
    is_premium BOOLEAN DEFAULT FALSE,
    profile_completed BOOLEAN DEFAULT FALSE,
    
    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for performance
CREATE INDEX idx_users_telegram_id ON users(telegram_id);
CREATE INDEX idx_users_location ON users USING GIST(location);
CREATE INDEX idx_users_active ON users(is_active) WHERE is_active = TRUE;
CREATE INDEX idx_users_gender_looking ON users(gender, looking_for);


-- Swipes table: Track all swipe actions
CREATE TABLE swipes (
    id SERIAL PRIMARY KEY,
    from_user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    to_user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    swipe_type VARCHAR(10) CHECK (swipe_type IN ('like', 'dislike', 'superlike')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    -- Prevent duplicate swipes
    UNIQUE(from_user_id, to_user_id)
);

-- Indexes for swipe queries
CREATE INDEX idx_swipes_from_user ON swipes(from_user_id);
CREATE INDEX idx_swipes_to_user ON swipes(to_user_id);
CREATE INDEX idx_swipes_type ON swipes(swipe_type);


-- Matches table: Mutual likes create matches
CREATE TABLE matches (
    id SERIAL PRIMARY KEY,
    user1_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    user2_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    matched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    -- Chat metadata
    last_message_at TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE,
    
    -- Ensure user1_id < user2_id for consistency
    CHECK (user1_id < user2_id),
    UNIQUE(user1_id, user2_id)
);

-- Indexes for match queries
CREATE INDEX idx_matches_user1 ON matches(user1_id);
CREATE INDEX idx_matches_user2 ON matches(user2_id);
CREATE INDEX idx_matches_active ON matches(is_active) WHERE is_active = TRUE;


-- Messages table: Chat messages between matches
CREATE TABLE messages (
    id SERIAL PRIMARY KEY,
    match_id INTEGER REFERENCES matches(id) ON DELETE CASCADE,
    from_user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    
    -- Message content
    message_type VARCHAR(10) CHECK (message_type IN ('text', 'image', 'video', 'gif')) DEFAULT 'text',
    content TEXT,
    media_url TEXT,
    
    -- Status
    is_read BOOLEAN DEFAULT FALSE,
    read_at TIMESTAMP,
    
    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for message queries
CREATE INDEX idx_messages_match ON messages(match_id);
CREATE INDEX idx_messages_from_user ON messages(from_user_id);
CREATE INDEX idx_messages_created_at ON messages(created_at DESC);


-- User blocks: Prevent unwanted interactions
CREATE TABLE user_blocks (
    id SERIAL PRIMARY KEY,
    blocker_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    blocked_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE(blocker_id, blocked_id)
);

-- Index for block checks
CREATE INDEX idx_blocks_blocker ON user_blocks(blocker_id);


-- Required interests for strict filtering mode
CREATE TABLE user_required_interests (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    interest VARCHAR(100) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, interest)
);

CREATE INDEX idx_user_required_interests_user ON user_required_interests(user_id);


-- Reports: User reporting system
CREATE TABLE reports (
    id SERIAL PRIMARY KEY,
    reporter_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    reported_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    reason VARCHAR(50) CHECK (reason IN ('inappropriate', 'spam', 'fake', 'harassment', 'other')),
    details TEXT,
    status VARCHAR(20) DEFAULT 'pending' CHECK (status IN ('pending', 'reviewed', 'resolved')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Index for admin review
CREATE INDEX idx_reports_status ON reports(status);


-- Notification settings: per-user notification controls
CREATE TABLE IF NOT EXISTS notification_settings (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    like_enabled BOOLEAN DEFAULT TRUE,
    match_enabled BOOLEAN DEFAULT TRUE,
    message_enabled BOOLEAN DEFAULT TRUE,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);


-- Function to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Trigger for users table
CREATE TRIGGER update_users_updated_at 
    BEFORE UPDATE ON users 
    FOR EACH ROW 
    EXECUTE FUNCTION update_updated_at_column();


-- Function to create match when two users like each other
CREATE OR REPLACE FUNCTION create_match_on_mutual_like()
RETURNS TRIGGER AS $$
DECLARE
    reverse_swipe RECORD;
    match_user1_id INTEGER;
    match_user2_id INTEGER;
BEGIN
    -- Only process 'like' and 'superlike' swipes
    IF NEW.swipe_type IN ('like', 'superlike') THEN
        -- Check if the other user also liked this user
        SELECT * INTO reverse_swipe
        FROM swipes
        WHERE from_user_id = NEW.to_user_id
          AND to_user_id = NEW.from_user_id
          AND swipe_type IN ('like', 'superlike');
        
        IF FOUND THEN
            -- Mutual like found! Create match
            -- Ensure user1_id < user2_id
            IF NEW.from_user_id < NEW.to_user_id THEN
                match_user1_id := NEW.from_user_id;
                match_user2_id := NEW.to_user_id;
            ELSE
                match_user1_id := NEW.to_user_id;
                match_user2_id := NEW.from_user_id;
            END IF;
            
            -- Insert match (ignore if already exists)
            INSERT INTO matches (user1_id, user2_id)
            VALUES (match_user1_id, match_user2_id)
            ON CONFLICT (user1_id, user2_id) DO NOTHING;
        END IF;
    END IF;
    
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Trigger to auto-create matches
CREATE TRIGGER trigger_create_match
    AFTER INSERT ON swipes
    FOR EACH ROW
    EXECUTE FUNCTION create_match_on_mutual_like();


-- Function to calculate age from birthdate
CREATE OR REPLACE FUNCTION calculate_age(birthdate DATE)
RETURNS INTEGER AS $$
BEGIN
    RETURN EXTRACT(YEAR FROM AGE(birthdate));
END;
$$ LANGUAGE plpgsql;


-- View: Get potential matches for a user (excluding already swiped)
CREATE OR REPLACE VIEW potential_matches AS
SELECT 
    u.id,
    u.telegram_id,
    u.first_name,
    u.username,
    u.bio,
    u.photos_urls,
    u.primary_photo_url,
    u.interests,
    u.gender,
    calculate_age(u.birthdate) as age,
    u.location,
    u.city
FROM users u
WHERE u.is_active = TRUE
  AND u.is_blocked = FALSE
  AND u.profile_completed = TRUE;


-- Seed data example (for testing)
INSERT INTO users (
    telegram_id, phone, country_code, username, first_name, gender, 
    birthdate, bio, looking_for, interests, profile_completed
) VALUES
    (123456789, '+1234567890', '+1', 'john_doe', 'John', 'male', 
     '1995-06-15', 'Adventure seeker and coffee enthusiast ☕', 'female',
     ARRAY['travel', 'coffee', 'hiking'], TRUE),
    (987654321, '+1987654321', '+1', 'jane_smith', 'Jane', 'female',
     '1998-03-22', 'Artist | Dog lover 🎨🐕', 'male',
     ARRAY['art', 'dogs', 'yoga'], TRUE);
