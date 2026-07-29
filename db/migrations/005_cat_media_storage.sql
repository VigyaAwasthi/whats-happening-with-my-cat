-- Private Supabase Storage bucket for cat profile and scrapbook media.
-- Object names must begin with the owning cat UUID: <cat_id>/<object-name>.

INSERT INTO storage.buckets (id, name, public)
VALUES ('cat-media', 'cat-media', false)
ON CONFLICT (id) DO UPDATE SET public = false;

DROP POLICY IF EXISTS cat_media_select_own_cat ON storage.objects;
CREATE POLICY cat_media_select_own_cat
ON storage.objects
FOR SELECT
TO authenticated
USING (
    bucket_id = 'cat-media'
    AND EXISTS (
        SELECT 1
        FROM public.cat_profiles AS cat
        JOIN public.accounts AS account ON account.id = cat.account_id
        WHERE cat.id::text = (storage.foldername(name))[1]
          AND account.auth_subject_id = auth.uid()
    )
);

DROP POLICY IF EXISTS cat_media_insert_own_cat ON storage.objects;
CREATE POLICY cat_media_insert_own_cat
ON storage.objects
FOR INSERT
TO authenticated
WITH CHECK (
    bucket_id = 'cat-media'
    AND EXISTS (
        SELECT 1
        FROM public.cat_profiles AS cat
        JOIN public.accounts AS account ON account.id = cat.account_id
        WHERE cat.id::text = (storage.foldername(name))[1]
          AND account.auth_subject_id = auth.uid()
    )
);

DROP POLICY IF EXISTS cat_media_update_own_cat ON storage.objects;
CREATE POLICY cat_media_update_own_cat
ON storage.objects
FOR UPDATE
TO authenticated
USING (
    bucket_id = 'cat-media'
    AND EXISTS (
        SELECT 1
        FROM public.cat_profiles AS cat
        JOIN public.accounts AS account ON account.id = cat.account_id
        WHERE cat.id::text = (storage.foldername(name))[1]
          AND account.auth_subject_id = auth.uid()
    )
)
WITH CHECK (
    bucket_id = 'cat-media'
    AND EXISTS (
        SELECT 1
        FROM public.cat_profiles AS cat
        JOIN public.accounts AS account ON account.id = cat.account_id
        WHERE cat.id::text = (storage.foldername(name))[1]
          AND account.auth_subject_id = auth.uid()
    )
);

DROP POLICY IF EXISTS cat_media_delete_own_cat ON storage.objects;
CREATE POLICY cat_media_delete_own_cat
ON storage.objects
FOR DELETE
TO authenticated
USING (
    bucket_id = 'cat-media'
    AND EXISTS (
        SELECT 1
        FROM public.cat_profiles AS cat
        JOIN public.accounts AS account ON account.id = cat.account_id
        WHERE cat.id::text = (storage.foldername(name))[1]
          AND account.auth_subject_id = auth.uid()
    )
);
