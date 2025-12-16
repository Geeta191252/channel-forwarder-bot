-- Create bot_config table to persist configuration
CREATE TABLE public.bot_config (
  id UUID NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  source_channel TEXT NOT NULL,
  dest_channel TEXT NOT NULL,
  created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
  updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
);

-- Only allow one config row
CREATE UNIQUE INDEX bot_config_single_row ON public.bot_config ((true));

-- Disable RLS for this internal config table (only used by edge function with service role)
ALTER TABLE public.bot_config ENABLE ROW LEVEL SECURITY;

-- Allow service role full access
CREATE POLICY "Service role can manage config" ON public.bot_config
  FOR ALL USING (true) WITH CHECK (true);