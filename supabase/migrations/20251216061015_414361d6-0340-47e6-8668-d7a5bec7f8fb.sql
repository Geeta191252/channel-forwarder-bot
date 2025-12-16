-- Create a table to persist forwarding progress
CREATE TABLE public.forwarding_progress (
  id TEXT PRIMARY KEY DEFAULT 'current',
  is_active BOOLEAN NOT NULL DEFAULT false,
  source_channel TEXT,
  dest_channel TEXT,
  start_id INTEGER,
  end_id INTEGER,
  current_batch INTEGER DEFAULT 0,
  total_batches INTEGER DEFAULT 0,
  success_count INTEGER DEFAULT 0,
  failed_count INTEGER DEFAULT 0,
  skipped_count INTEGER DEFAULT 0,
  total_count INTEGER DEFAULT 0,
  rate_limit_hits INTEGER DEFAULT 0,
  speed NUMERIC DEFAULT 0,
  started_at TIMESTAMP WITH TIME ZONE,
  last_updated_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
  stop_requested BOOLEAN DEFAULT false
);

-- Disable RLS for this table (service role only)
ALTER TABLE public.forwarding_progress ENABLE ROW LEVEL SECURITY;

-- Allow service role full access
CREATE POLICY "Service role has full access to forwarding_progress"
ON public.forwarding_progress
FOR ALL
USING (true)
WITH CHECK (true);