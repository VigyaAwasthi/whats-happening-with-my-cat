-- Add an optional, owner-reported sex value without changing any safety routing.
DO $$
BEGIN
    CREATE TYPE cat_sex AS ENUM ('male', 'female', 'unknown');
EXCEPTION
    WHEN duplicate_object THEN NULL;
END
$$;

ALTER TABLE cat_profiles
    ADD COLUMN IF NOT EXISTS sex cat_sex DEFAULT 'unknown';

-- Keep existing profiles behaviorally unchanged while making their API value explicit.
UPDATE cat_profiles
SET sex = 'unknown'
WHERE sex IS NULL;

COMMENT ON COLUMN cat_profiles.sex IS
    'Optional owner-reported sex. Unknown is valid; safety rules remain conservative regardless.';
