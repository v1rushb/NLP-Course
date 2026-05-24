import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { useAuth } from "@/lib/auth";
import { api, type ChatMsg } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import {
  Bot,
  MessageCircle,
  RotateCcw,
  Send,
  Sparkles,
  Trash2,
  User as UserIcon,
  WifiOff,
} from "lucide-react";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/chat")({
  component: ChatPage,
});

const SUGGESTIONS = [
  "اعرض علاماتي",
  "علامة CS101",
  "موادي المسجلة",
  "سجلني في CS101",
];

function ChatPage() {
  const { user } = useAuth();
  const nav = useNavigate();
  const [msgs, setMsgs] = useState<ChatMsg[]>([]);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [failedQuery, setFailedQuery] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!user) {
      nav({ to: "/" });
      return;
    }
    api.history(user.id).then(setMsgs).catch(() => {
      setFailedQuery(null);
    });
  }, [user, nav]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [msgs, sending, failedQuery]);

  const send = async (q?: string, retry = false) => {
    const query = (q ?? text).trim();
    if (!query || !user || sending) return;

    setText("");
    setFailedQuery(null);
    setSending(true);

    if (!retry) {
      setMsgs((current) => [
        ...current,
        { id: Date.now(), user_id: user.id, role: "user", text: query, ts: Date.now() / 1000 },
      ]);
    }

    try {
      const result = await api.chat(user.id, query);
      setMsgs((current) => [
        ...current,
        {
          id: Date.now() + 1,
          user_id: user.id,
          role: "assistant",
          text: result.response,
          ts: Date.now() / 1000,
        },
      ]);
    } catch (error: any) {
      setFailedQuery(query);
      toast.error(error.message || "تعذر إرسال الرسالة.");
    } finally {
      setSending(false);
    }
  };

  const clear = async () => {
    if (!user) return;
    await api.clearHistory(user.id);
    setMsgs([]);
    setFailedQuery(null);
    toast.success("تم مسح المحادثة.");
  };

  return (
    <AppShell>
      <div className="mb-4 flex items-end justify-between gap-2">
        <div>
          <h1 className="font-ruqaa text-3xl font-bold">المساعد الأكاديمي</h1>
          <p className="text-sm text-muted-foreground">
            اسأل عن المواد، التسجيل، العلامات، وخدمات الجامعة بلغة عربية واضحة.
          </p>
        </div>
        {msgs.length > 0 && (
          <Button variant="outline" size="sm" onClick={clear}>
            <Trash2 className="ml-1 h-4 w-4" /> مسح السجل
          </Button>
        )}
      </div>

      <Card className="chat-arabic-surface flex h-[70vh] flex-col overflow-hidden">
        <div ref={scrollRef} className="flex-1 space-y-4 overflow-y-auto p-4">
          {msgs.length === 0 && !sending && (
            <div className="flex h-full flex-col items-center justify-center gap-4 text-center">
              <div className="grid h-16 w-16 place-items-center rounded-2xl bg-primary/10 text-primary">
                <Sparkles className="h-7 w-7" />
              </div>
              <div>
                <div className="font-ruqaa text-2xl font-semibold">كيف أخدمك اليوم؟</div>
                <div className="text-sm text-muted-foreground">
                  اختر عبارة سريعة أو اكتب سؤالك، وسأحافظ على سياق الحديث.
                </div>
              </div>
              <div className="flex flex-wrap justify-center gap-2">
                {SUGGESTIONS.map((suggestion) => (
                  <button
                    key={suggestion}
                    onClick={() => send(suggestion)}
                    className="rounded-full border bg-card px-3 py-1.5 text-sm transition-colors hover:bg-accent"
                  >
                    {suggestion}
                  </button>
                ))}
              </div>
            </div>
          )}

          {msgs.map((msg) => (
            <Bubble key={msg.id} msg={msg} />
          ))}

          {sending && (
            <Bubble msg={{ id: -1, user_id: 0, role: "assistant", text: "", ts: 0 }} typing />
          )}

          {failedQuery && !sending && (
            <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm">
              <div className="flex items-start gap-2">
                <WifiOff className="mt-0.5 h-4 w-4 text-destructive" />
                <div className="flex-1">
                  <div className="font-medium text-destructive">لم تُرسل آخر رسالة</div>
                  <div className="mt-1 text-muted-foreground">
                    أعد المحاولة عندما يستقر الاتصال.
                  </div>
                </div>
                <Button size="sm" variant="outline" onClick={() => send(failedQuery, true)}>
                  <RotateCcw className="ml-1 h-4 w-4" /> إعادة المحاولة
                </Button>
              </div>
            </div>
          )}
        </div>

        <form
          onSubmit={(event) => {
            event.preventDefault();
            send();
          }}
          className="flex gap-2 border-t bg-card p-3"
        >
          <Input
            value={text}
            onChange={(event) => setText(event.target.value)}
            placeholder="اكتب رسالتك هنا..."
            disabled={sending}
          />
          <Button type="submit" disabled={sending || !text.trim()} aria-label="إرسال الرسالة">
            <Send className="h-4 w-4" />
          </Button>
        </form>
      </Card>
    </AppShell>
  );
}

function Bubble({ msg, typing }: { msg: ChatMsg; typing?: boolean }) {
  const isUser = msg.role === "user";
  return (
    <div className={cn("flex gap-3", isUser ? "flex-row" : "flex-row-reverse")}>
      <div
        className={cn(
          "grid h-9 w-9 shrink-0 place-items-center rounded-full",
          isUser ? "bg-primary text-primary-foreground" : "bg-muted text-foreground"
        )}
      >
        {isUser ? <UserIcon className="h-4 w-4" /> : <Bot className="h-4 w-4" />}
      </div>
      <div
        className={cn(
          "max-w-[75%] whitespace-pre-wrap rounded-2xl px-4 py-2.5 text-sm leading-relaxed",
          isUser ? "rounded-tr-sm bg-primary text-primary-foreground" : "rounded-tl-sm bg-muted"
        )}
        dir="rtl"
      >
        {typing ? <TypingDots /> : msg.text}
      </div>
    </div>
  );
}

function TypingDots() {
  return (
    <span className="inline-flex items-center gap-2">
      <MessageCircle className="h-3.5 w-3.5" />
      <span className="inline-flex items-center gap-1">
        <span className="h-2 w-2 animate-bounce rounded-full bg-current [animation-delay:-0.3s]" />
        <span className="h-2 w-2 animate-bounce rounded-full bg-current [animation-delay:-0.15s]" />
        <span className="h-2 w-2 animate-bounce rounded-full bg-current" />
      </span>
      <span className="text-xs text-muted-foreground">جار التفكير</span>
    </span>
  );
}
