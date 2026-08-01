BEGIN;

-- Fix cat-media Storage policies created by migration 005.
-- The unqualified `name` reference resolved to cat_profiles.name instead of
-- storage.objects.name, preventing the object's leading cat UUID from being
-- checked correctly.

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
        JOIN public.accounts AS account
          ON account.id = cat.account_id
        WHERE cat.id::text =
              (storage.foldername(storage.objects.name))[1]
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
        JOIN public.accounts AS account
          ON account.id = cat.account_id
        WHERE cat.id::text =
              (storage.foldername(storage.objects.name))[1]
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
        JOIN public.accounts AS account
          ON account.id = cat.account_id
        WHERE cat.id::text =
              (storage.foldername(storage.objects.name))[1]
          AND account.auth_subject_id = auth.uid()
    )
)
WITH CHECK (
    bucket_id = 'cat-media'
    AND EXISTS (
        SELECT 1
        FROM public.cat_profiles AS cat
        JOIN public.accounts AS account
          ON account.id = cat.account_id
        WHERE cat.id::text =
              (storage.foldername(storage.objects.name))[1]
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
        JOIN public.accounts AS account
          ON account.id = cat.account_id
        WHERE cat.id::text =
              (storage.foldername(storage.objects.name))[1]
          AND account.auth_subject_id = auth.uid()
    )
);

COMMIT;
