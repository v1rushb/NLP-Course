import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { useAuth } from "@/lib/auth";
import { api, type Course } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { toast } from "sonner";
import { Award, Clock, Loader2, Trash2, User2 } from "lucide-react";

export const Route = createFileRoute("/my")({
  component: MyCoursesPage,
});

function MyCoursesPage() {
  const { user } = useAuth();
  const nav = useNavigate();
  const [list, setList] = useState<Course[]>([]);
  const [loading, setLoading] = useState(true);
  const [droppingId, setDroppingId] = useState<number | null>(null);

  const load = async () => {
    if (!user) return;
    try {
      const courses = await api.myCourses(user.id);
      setList(courses);
    } catch (error: any) {
      toast.error(error.message || "Your courses could not be loaded.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!user) {
      nav({ to: "/" });
      return;
    }

    if (user.role === "admin") {
      nav({ to: "/admin" });
      return;
    }

    load();
  }, [user, nav]);

  const drop = async (course: Course) => {
    if (!user) return;
    setDroppingId(course.id);
    try {
      await api.drop(user.id, course.id);
      toast.success("Course removed from your schedule.");
      await load();
    } catch (error: any) {
      toast.error(error.message || "Course could not be removed.");
    } finally {
      setDroppingId(null);
    }
  };

  const graded = list.filter((course) => course.grade != null);
  const avg = graded.length
    ? Math.round((graded.reduce((total, course) => total + (course.grade || 0), 0) / graded.length) * 10) / 10
    : null;

  if (user?.role === "admin") return null;

  return (
    <AppShell>
      <div className="mb-6">
        <h1 className="text-2xl font-bold">My Courses</h1>
        <p className="text-sm text-muted-foreground">Your current schedule and recorded grades.</p>
      </div>

      <div className="mb-6 grid gap-4 sm:grid-cols-3">
        <Stat label="Enrolled courses" value={list.length.toString()} />
        <Stat label="Recorded grades" value={graded.length.toString()} />
        <Stat label="Average grade" value={avg == null ? "Not available" : String(avg)} highlight={avg != null} />
      </div>

      {loading ? (
        <div className="grid gap-4 md:grid-cols-2">
          {Array.from({ length: 4 }).map((_, index) => (
            <Card key={index} className="h-40 animate-pulse" />
          ))}
        </div>
      ) : list.length === 0 ? (
        <div className="rounded-lg border border-dashed py-16 text-center">
          <p className="text-muted-foreground">You are not enrolled in any courses yet.</p>
          <Button className="mt-4" onClick={() => nav({ to: "/courses" })}>
            Browse courses
          </Button>
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {list.map((course) => (
            <Card key={course.id}>
              <CardHeader className="pb-2">
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <Badge variant="secondary" className="mb-2">
                      {course.code}
                    </Badge>
                    <CardTitle className="text-base">{course.name}</CardTitle>
                  </div>

                  {course.grade != null && (
                    <div className="grid h-12 w-12 place-items-center rounded-xl bg-primary text-primary-foreground">
                      <span className="text-sm font-bold">{course.grade}</span>
                    </div>
                  )}
                </div>
              </CardHeader>

              <CardContent>
                <div className="space-y-1.5 text-sm text-muted-foreground">
                  <div className="flex items-center gap-2">
                    <User2 className="h-4 w-4" /> {course.doctor}
                  </div>
                  <div className="flex items-center gap-2">
                    <Clock className="h-4 w-4" /> {course.days} - {course.time}
                  </div>

                  {course.grade == null && (
                    <div className="flex items-center gap-2 text-amber-600">
                      <Award className="h-4 w-4" /> Grade not recorded
                    </div>
                  )}
                </div>

                <Button
                  variant="outline"
                  size="sm"
                  className="mt-4"
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
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </AppShell>
  );
}

function Stat({
  label,
  value,
  highlight,
}: {
  label: string;
  value: string;
  highlight?: boolean;
}) {
  return (
    <Card>
      <CardContent className="p-5">
        <div className="text-sm text-muted-foreground">{label}</div>
        <div className={"mt-1 text-3xl font-bold " + (highlight ? "text-primary" : "")}>
          {value}
        </div>
      </CardContent>
    </Card>
  );
}
