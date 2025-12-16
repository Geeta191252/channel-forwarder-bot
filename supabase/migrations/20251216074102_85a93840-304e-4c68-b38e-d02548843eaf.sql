-- Create user_sessions table for wizard state tracking
CREATE TABLE IF NOT EXISTS public.user_sessions (
  id UUID NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id BIGINT NOT NULL UNIQUE,
  state TEXT DEFAULT 'idle',
  source_channel TEXT,
  source_title TEXT,
  dest_channel TEXT,
  dest_title TEXT,
  skip_number INTEGER DEFAULT 0,
  last_message_id BIGINT,
  created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
  updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
);

-- Enable RLS
ALTER TABLE public.user_sessions ENABLE ROW LEVEL SECURITY;

-- Allow public access for edge functions
CREATE POLICY "Allow all operations on user_sessions" 
ON public.user_sessions 
FOR ALL 
USING (true)
WITH CHECK (true);