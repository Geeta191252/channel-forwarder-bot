-- Create table to track forwarded messages
CREATE TABLE public.forwarded_messages (
  id UUID NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  source_channel TEXT NOT NULL,
  dest_channel TEXT NOT NULL,
  source_message_id BIGINT NOT NULL,
  forwarded_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
  UNIQUE(source_channel, dest_channel, source_message_id)
);

-- Create index for fast lookups
CREATE INDEX idx_forwarded_messages_lookup 
ON public.forwarded_messages(source_channel, dest_channel, source_message_id);

-- Enable RLS but allow public access (no auth required for this bot)
ALTER TABLE public.forwarded_messages ENABLE ROW LEVEL SECURITY;

-- Allow all operations (this is a bot utility table)
CREATE POLICY "Allow all operations on forwarded_messages" 
ON public.forwarded_messages 
FOR ALL 
USING (true) 
WITH CHECK (true);