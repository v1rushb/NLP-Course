import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { useAuth } from "@/lib/auth";
import { api, type Course } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import {
  BookOpen,
  CheckCircle2,
  Clock,
  Loader2,
  MessageCircle,
  Search,
  Trash2,
  User2,
} from "lucide-react";

export const Route = createFileRoute("/courses")({
  component: CoursesPage,
});

function CoursesPage() {
  const { user } = useAuth();
  const nav = useNavigate();
  const [courses, setCourses] = useState<Course[]>([]);
  const [enrolled, setEnrolled] = useState<Course[]>([]);
  const [q, setQ] = useState("");
  const [loading, setLoading] = useState(true);
  const [enrollingId, setEnrollingId] = useState<number | null>(null);
  const [droppingId, setDroppingId] = useState<number | null>(null);

  const isAdmin = user?.role === "admin";

  const load = async (showLoading = true) => {
    if (!user) return;
    if (showLoading) setLoading(true);
    try {
      const [allCourses, myCourses] = await Promise.all([
        api.courses(),
        isAdmin ? Promise.resolve([]) : api.myCourses(user.id),
      ]);
      setCourses(allCourses);
      setEnrolled(myCourses);
    } catch (error: any) {
      toast.error(error.message || "Courses could not be loaded.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!user) {
      nav({ to: "/" });
      return;
    }
    load();
  }, [user, nav]);

  const enrolledIds = useMemo(() => new Set(enrolled.map((course) => course.id)), [enrolled]);
  const available = courses.filter((course) => !enrolledIds.has(course.id));
  const filtered = available.filter((course) => {
    const query = q.toLowerCase();
    return (
      course.name.toLowerCase().includes(query) ||
      course.code.toLowerCase().includes(query) ||
      course.doctor.toLowerCase().includes(query)
    );
  });

  const enroll = async (course: Course) => {
    if (!user) return;
    const previous = enrolled;
    setEnrollingId(course.id);
    setEnrolled((list) => [...list, { ...course, grade: null }]);
    try {
      await api.enroll(user.id, course.id);
      toast.success("Course added to your schedule.");
      await load(false);
    } catch (error: any) {
      setEnrolled(previous);
      toast.error(error.message || "Course could not be added.");
    } finally {
      setEnrollingId(null);
    }
  };

  const drop = async (course: Course) => {
    if (!user) return;
    const previous = enrolled;
    setDroppingId(course.id);
    setEnrolled((list) => list.filter((item) => item.id !== course.id));
    try {
      await api.drop(user.id, course.id);
      toast.success("Course removed from your schedule.");
      await load(false);
    } catch (error: any) {
      setEnrolled(previous);
      toast.error(error.message || "Course could not be removed.");
    } finally {
      setDroppingId(null);
    }
  };

  return (
    <AppShell>
      <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">Available Courses</h1>
          <p className="text-sm text-muted-foreground">
            Browse open sections and manage your current schedule.
          </p>
        </div>

        <div className="relative w-full max-w-xs">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            className="pl-9"
            placeholder="Search courses"
            value={q}
            onChange={(event) => setQ(event.target.value)}
          />
        </div>
      </div>

      {loading ? (
        <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
          <div className="grid gap-4 md:grid-cols-2">
            {Array.from({ length: 4 }).map((_, index) => (
              <Card key={index} className="h-56 animate-pulse" />
            ))}
          </div>
          {!isAdmin && <Card className="h-72 animate-pulse" />}
        </div>
      ) : (
        <div className={isAdmin ? "" : "grid gap-6 lg:grid-cols-[1fr_320px]"}>
          <section>
            <div className="grid gap-4 md:grid-cols-2">
              {filtered.map((course) => (
                <CourseCard
                  key={course.id}
                  course={course}
                  isAdmin={!!isAdmin}
                  pending={enrollingId === course.id}
                  onEnroll={() => enroll(course)}
                />
              ))}

              {filtered.length === 0 && (
                <div className="col-span-full rounded-lg border border-dashed py-12 text-center text-muted-foreground">
                  {q ? `No courses match "${q}".` : "No courses are currently open for enrollment."}
                </div>
              )}
            </div>
          </section>

          {!isAdmin && (
            <aside className="lg:sticky lg:top-24 lg:self-start">
              <Card>
                <CardHeader className="pb-3">
                  <div className="flex items-center justify-between gap-2">
                    <CardTitle className="text-base">Currently Enrolled Courses</CardTitle>
                    <Badge variant="secondary">{enrolled.length}</Badge>
                  </div>
                </CardHeader>
                <CardContent>
                  {enrolled.length === 0 ? (
                    <div className="rounded-lg border border-dashed p-5 text-center text-sm text-muted-foreground">
                      Your schedule is empty.
                    </div>
                  ) : (
                    <div className="space-y-3">
                      {enrolled.map((course) => (
                        <div key={course.id} className="rounded-lg border p-3">
                          <div className="mb-2 flex items-start justify-between gap-2">
                            <div>
                              <Badge variant="secondary" className="mb-1">
                                {course.code}
                              </Badge>
                              <div className="text-sm font-medium leading-tight">{course.name}</div>
                            </div>
                            <CheckCircle2 className="h-4 w-4 text-primary" />
                          </div>
                          <div className="mb-3 text-xs text-muted-foreground">
                            {course.days} - {course.time}
                          </div>
                          <Button
                            variant="outline"
                            size="sm"
                            className="w-full"
                            onClick={() => drop(course)}
                            disabled={droppingId === course.id}
                          >
                            {droppingId === course.id ? (
                              <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                            ) : (
                              <Trash2 className="mr-1 h-4 w-4" />
                            )}
                            Drop course
                          </Button>
                        </div>
                      ))}
                    </div>
                  )}
                </CardContent>
              </Card>
            </aside>
          )}
        </div>
      )}

      <div className="mt-8 rounded-lg border bg-card p-5">
        <div className="flex flex-wrap items-center gap-3">
          <MessageCircle className="h-6 w-6 text-primary" />
          <div className="flex-1">
            <div className="font-semibold">Need help with registration?</div>
            <div className="text-sm text-muted-foreground">
              The assistant can check your courses, enrollment status, and grades.
            </div>
          </div>
          <Button variant="outline" onClick={() => nav({ to: "/chat" })}>
            Open assistant
          </Button>
        </div>
      </div>
    </AppShell>
  );
}

function CourseCard({
  course,
  isAdmin,
  pending,
  onEnroll,
}: {
  course: Course;
  isAdmin: boolean;
  pending: boolean;
  onEnroll: () => void;
}) {
  const seats = course.available_seats ?? 0;
  const full = seats <= 0;

  return (
    <Card className="group transition-shadow hover:shadow-lg">
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between gap-2">
          <div>
            <Badge variant="secondary" className="mb-2">
              {course.code}
            </Badge>
            <CardTitle className="text-base leading-tight">{course.name}</CardTitle>
          </div>

          <span className="grid h-10 w-10 place-items-center rounded-xl bg-primary/10 text-primary">
            <BookOpen className="h-5 w-5" />
          </span>
        </div>
      </CardHeader>

      <CardContent className="space-y-3">
        <div className="space-y-1.5 text-sm text-muted-foreground">
          <div className="flex items-center gap-2">
            <User2 className="h-4 w-4" /> {course.doctor}
          </div>
          <div className="flex items-center gap-2">
            <Clock className="h-4 w-4" /> {course.days} - {course.time}
          </div>
        </div>

        <div className="flex items-center justify-between rounded-md bg-muted/50 p-2 text-xs">
          <span>Seats available</span>
          <span className={full ? "font-bold text-destructive" : "font-bold text-primary"}>
            {seats} / {course.capacity}
          </span>
        </div>

        {!isAdmin && (
          <Button className="w-full" disabled={full || pending} onClick={onEnroll}>
            {pending && <Loader2 className="mr-1 h-4 w-4 animate-spin" />}
            {full ? "Full" : pending ? "Adding..." : "Enroll"}
          </Button>
        )}
      </CardContent>
    </Card>
  );
}
