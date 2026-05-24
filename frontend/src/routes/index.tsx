import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect, useState, type FormEvent } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { InputOTP, InputOTPGroup, InputOTPSlot } from "@/components/ui/input-otp";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { toast } from "sonner";
import { GraduationCap, Sparkles, BookOpen, ShieldCheck, MailCheck, RefreshCw } from "lucide-react";

export const Route = createFileRoute("/")({
  component: AuthPage,
});

function AuthPage() {
  const { user, setUser } = useAuth();
  const nav = useNavigate();

  useEffect(() => {
    if (user) nav({ to: "/courses" });
  }, [user, nav]);

  return (
    <div dir="ltr" className="min-h-screen bg-gradient-to-br from-background via-background to-primary/5">
      <div className="mx-auto grid min-h-screen max-w-6xl items-center gap-10 px-6 py-10 lg:grid-cols-2">
        <div className="space-y-6">
          <div className="inline-flex items-center gap-2 rounded-full border bg-card px-3 py-1 text-xs text-muted-foreground">
            <Sparkles className="h-3.5 w-3.5 text-primary" />
            Palestine Polytechnic University
          </div>
          <h1 className="text-4xl font-bold leading-tight md:text-5xl">
            Courses, grades, and academic support
            <br />
            <span className="text-primary">in one secure workspace</span>
          </h1>
          <p className="max-w-md text-muted-foreground">
            Sign in with your university account to manage enrollment, review academic records,
            and use the assistant for university services.
          </p>

          <div className="grid gap-3 sm:grid-cols-3">
            <Feature icon={BookOpen} title="Courses" desc="Browse and enroll" />
            <Feature icon={Sparkles} title="Assistant" desc="Context-aware help" />
            <Feature icon={ShieldCheck} title="Security" desc="Email verification" />
          </div>
        </div>

        <Card className="mx-auto w-full max-w-md shadow-xl">
          <CardHeader className="text-center">
            <div className="mx-auto mb-2 grid h-12 w-12 place-items-center rounded-2xl bg-primary text-primary-foreground">
              <GraduationCap className="h-6 w-6" />
            </div>
            <CardTitle className="text-2xl">Welcome</CardTitle>
            <CardDescription>Access your student workspace</CardDescription>
          </CardHeader>
          <CardContent>
            <Tabs defaultValue="login">
              <TabsList className="grid w-full grid-cols-2">
                <TabsTrigger value="login">Sign in</TabsTrigger>
                <TabsTrigger value="signup">Create account</TabsTrigger>
              </TabsList>
              <TabsContent value="login" className="pt-4">
                <LoginForm onDone={(u) => { setUser(u); toast.success(`Welcome, ${u.name || "student"}`); nav({ to: "/courses" }); }} />
              </TabsContent>
              <TabsContent value="signup" className="pt-4">
                <SignupForm onDone={(u) => { setUser(u); toast.success("Account verified"); nav({ to: "/courses" }); }} />
              </TabsContent>
            </Tabs>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function Feature({ icon: Icon, title, desc }: any) {
  return (
    <div className="rounded-xl border bg-card p-3">
      <Icon className="mb-2 h-5 w-5 text-primary" />
      <div className="text-sm font-medium">{title}</div>
      <div className="text-xs text-muted-foreground">{desc}</div>
    </div>
  );
}

function LoginForm({ onDone }: { onDone: (u: any) => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);

  return (
    <form
      className="space-y-3"
      onSubmit={async (e) => {
        e.preventDefault();
        setLoading(true);
        try {
          const u = await api.login(email, password);
          onDone(u);
        } catch (err: any) {
          toast.error(err.message || "Unable to sign in");
        } finally {
          setLoading(false);
        }
      }}
    >
      <div className="space-y-1.5">
        <Label htmlFor="le">University email</Label>
        <Input id="le" dir="ltr" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="lp">Password</Label>
        <Input id="lp" type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
      </div>
      <Button type="submit" className="w-full" disabled={loading}>
        {loading ? "Signing in..." : "Sign in"}
      </Button>
    </form>
  );
}

function SignupForm({ onDone }: { onDone: (u: any) => void }) {
  const [form, setForm] = useState({ name: "", email: "", student_id: "", password: "" });
  const [phase, setPhase] = useState<"form" | "otp">("form");
  const [pendingEmail, setPendingEmail] = useState("");
  const [otp, setOtp] = useState("");
  const [resendIn, setResendIn] = useState(0);
  const [expiresIn, setExpiresIn] = useState(0);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (phase !== "otp") return;
    const timer = window.setInterval(() => {
      setResendIn((value) => Math.max(0, value - 1));
      setExpiresIn((value) => Math.max(0, value - 1));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [phase]);

  const submitSignup = async (e: FormEvent) => {
    e.preventDefault();
    setLoading(true);
    try {
      const res = await api.signup(form);
      setPendingEmail(res.email);
      setResendIn(res.resend_after);
      setExpiresIn(res.expires_in);
      setOtp("");
      setPhase("otp");
      toast.success("Verification code sent");
    } catch (err: any) {
      toast.error(err.message || "Unable to create account");
    } finally {
      setLoading(false);
    }
  };

  const verifySignup = async (e: FormEvent) => {
    e.preventDefault();
    if (otp.length !== 6) {
      toast.error("Enter the 6-digit verification code");
      return;
    }
    setLoading(true);
    try {
      const user = await api.verifySignupOtp(pendingEmail, otp);
      onDone(user);
    } catch (err: any) {
      toast.error(err.message || "Invalid verification code");
      if ((err.message || "").toLowerCase().includes("expired")) setPhase("form");
    } finally {
      setLoading(false);
    }
  };

  const resend = async () => {
    setLoading(true);
    try {
      const res = await api.resendSignupOtp(pendingEmail);
      setOtp("");
      setResendIn(res.resend_after);
      setExpiresIn(res.expires_in);
      toast.success("New code sent");
    } catch (err: any) {
      toast.error(err.message || "Unable to send a new code");
    } finally {
      setLoading(false);
    }
  };

  if (phase === "otp") {
    return (
      <form className="space-y-4" onSubmit={verifySignup}>
        <div className="rounded-lg border bg-muted/40 p-4 text-center">
          <div className="mx-auto mb-3 grid h-11 w-11 place-items-center rounded-xl bg-primary/10 text-primary">
            <MailCheck className="h-5 w-5" />
          </div>
          <div className="font-medium">Check your email</div>
          <div className="mt-1 text-sm text-muted-foreground" dir="ltr">
            {pendingEmail}
          </div>
          <div className="mt-2 text-xs text-muted-foreground">
            Code expires in {Math.max(1, Math.ceil(expiresIn / 60))} minutes
          </div>
        </div>

        <div className="flex justify-center" dir="ltr">
          <InputOTP maxLength={6} value={otp} onChange={setOtp} disabled={loading}>
            <InputOTPGroup>
              {Array.from({ length: 6 }).map((_, index) => (
                <InputOTPSlot key={index} index={index} />
              ))}
            </InputOTPGroup>
          </InputOTP>
        </div>

        <Button type="submit" className="w-full" disabled={loading || otp.length !== 6}>
          {loading ? "Verifying..." : "Verify account"}
        </Button>

        <div className="flex items-center justify-between gap-2 text-sm">
          <Button type="button" variant="ghost" size="sm" onClick={() => { setPhase("form"); setOtp(""); }}>
            Edit details
          </Button>
          <Button type="button" variant="outline" size="sm" onClick={resend} disabled={loading || resendIn > 0}>
            <RefreshCw className="mr-1 h-4 w-4" />
            {resendIn > 0 ? `Resend in ${resendIn}s` : "Resend code"}
          </Button>
        </div>
      </form>
    );
  }

  return (
    <form className="space-y-3" onSubmit={submitSignup}>
      <div className="grid grid-cols-2 gap-3">
        <div className="col-span-2 space-y-1.5">
          <Label>Full name</Label>
          <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required />
        </div>
        <div className="space-y-1.5">
          <Label>Student ID</Label>
          <Input dir="ltr" value={form.student_id} onChange={(e) => setForm({ ...form, student_id: e.target.value })} />
        </div>
        <div className="space-y-1.5">
          <Label>Password</Label>
          <Input type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} required />
        </div>
        <div className="col-span-2 space-y-1.5">
          <Label>University email</Label>
          <Input dir="ltr" type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} required />
        </div>
      </div>
      <Button type="submit" className="w-full" disabled={loading}>
        {loading ? "Sending verification..." : "Create account"}
      </Button>
    </form>
  );
}
