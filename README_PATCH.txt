TGSCRAPER v7 PATCH — 1 changed file (overwrite, push, redeploy)
================================================================
  forwarder.py — NO MORE 'FORWARDED FROM' TAG. Both the cover post and the
  videos/srt are now COPIED to your DB channel (sent as fresh messages from
  your account) instead of forwarded:
   - cover post: media + caption + original inline buttons + spoiler flag
   - videos in the same album stay grouped together
   - no source-channel tag on anything

Note: copying downloads+re-uploads media through the userbot connection —
slightly slower than forwarding, but tag-free.
