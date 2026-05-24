import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { useAuth } from "@/lib/auth";
import { api, type AdminStudent } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { toast } from "sonner";
import { BookOpen, ClipboardList, GraduationCap, Loader2, MessageSquare, Save, Users } from "lucide-react";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/admin")({
  component: AdminPage,
});

type PendingGrade = {
  studentId: number;
  courseId: number;
  code: string;
  grade: number | null;
};

function AdminPage() {
  const { user } = useAuth();
  const nav = useNavigate();
  const [data, setData] = useState<any | null>(null);
  const [students, setStudents] = useState<AdminStudent[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [gradeDrafts, setGradeDrafts] = useState<Record<string, string>>({});
  const [pendingGrade, setPendingGrade] = useState<PendingGrade | null>(null);
  const [savingKey, setSavingKey] = useState<string | null>(null);

  useEffect(() => {
    if (!user) {
      nav({ to: "/" });
      return;
    }
    if (user.role !== "admin") {
      nav({ to: "/courses" });
      return;
    }

    Promise.all([api.adminOverview(user.id), api.adminStudents(user.id)])
      .then(([overview, studentList]) => {
        setData(overview);
        setStudents(studentList);
        setSelectedId((current) => current ?? studentList[0]?.profile.id ?? null);
        setGradeDrafts(makeDrafts(studentList));
      })
      .catch((error) => toast.error(error.message || "Admin data could not be loaded."));
  }, [user, nav]);

  const selectedStudent = useMemo(
    () => students.find((student) => student.profile.id === selectedId) ?? null,
    [students, selectedId]
  );

  const requestGradeSave = (student: AdminStudent, course: any) => {
    const key = gradeKey(student.profile.id, course.course_id);
    const raw = (gradeDrafts[key] ?? "").trim();
    const grade = raw === "" ? null : Number(raw);

    if (raw !== "" && (Number.isNaN(grade) || grade < 0 || grade > 100)) {
      toast.error("Grade must be a number from 0 to 100.");
      return;
    }

    setPendingGrade({
      studentId: student.profile.id,
      courseId: course.course_id,
      code: course.code,
      grade,
    });
  };

  const commitGrade = async () => {
    if (!user || !pendingGrade) return;
    const key = gradeKey(pendingGrade.studentId, pendingGrade.courseId);
    setSavingKey(key);
    try {
      const result = await api.updateStudentGrade(
        user.id,
        pendingGrade.studentId,
        pendingGrade.courseId,
        pendingGrade.grade
      );
      setStudents((list) =>
        list.map((student) =>
          student.profile.id === pendingGrade.studentId ? result.student : student
        )
      );
      setGradeDrafts((drafts) => ({
        ...drafts,
        [key]: pendingGrade.grade == null ? "" : String(pendingGrade.grade),
      }));
      setPendingGrade(null);
      toast.success("Grade updated.");
    } catch (error: any) {
      toast.error(error.message || "Grade could not be updated.");
    } finally {
      setSavingKey(null);
    }
  };

  if (!data) {
    return (
      <AppShell>
        <div className="grid gap-4 md:grid-cols-4">
          {Array.from({ length: 4 }).map((_, index) => (
            <Card key={index} className="h-24 animate-pulse" />
          ))}
        </div>
      </AppShell>
    );
  }

  return (
    <AppShell>
      <div className="mb-6">
        <h1 className="text-2xl font-bold">Admin</h1>
        <p className="text-sm text-muted-foreground">
          Manage students, courses, enrollments, and recorded grades.
        </p>
      </div>

      <div className="mb-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard icon={Users} label="Total users" value={data.totals.users} />
        <StatCard icon={GraduationCap} label="Students" value={data.totals.students} />
        <StatCard icon={BookOpen} label="Courses" value={data.totals.courses} />
        <StatCard icon={ClipboardList} label="Enrollments" value={data.totals.enrollments} />
      </div>

      <Tabs defaultValue="students">
        <TabsList>
          <TabsTrigger value="students">Students</TabsTrigger>
          <TabsTrigger value="courses">Courses</TabsTrigger>
          <TabsTrigger value="users">Users</TabsTrigger>
          <TabsTrigger value="chat">Chat</TabsTrigger>
        </TabsList>

        <TabsContent value="students" className="pt-4">
          <div className="grid gap-4 lg:grid-cols-[300px_1fr]">
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-base">Students</CardTitle>
              </CardHeader>
              <CardContent>
                {students.length === 0 ? (
                  <div className="rounded-lg border border-dashed p-5 text-center text-sm text-muted-foreground">
                    No students found.
                  </div>
                ) : (
                  <div className="space-y-2">
                    {students.map((student) => (
                      <button
                        key={student.profile.id}
                        className={cn(
                          "w-full rounded-lg border p-3 text-left transition-colors hover:bg-accent",
                          selectedId === student.profile.id && "border-primary bg-primary/5"
                        )}
                        onClick={() => setSelectedId(student.profile.id)}
                      >
                        <div className="font-medium">{student.profile.name}</div>
                        <div className="mt-1 text-xs text-muted-foreground">
                          {student.profile.student_id || student.profile.email}
                        </div>
                      </button>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>

            {selectedStudent ? (
              <StudentDetail
                student={selectedStudent}
                gradeDrafts={gradeDrafts}
                savingKey={savingKey}
                onDraftChange={(key, value) => setGradeDrafts((drafts) => ({ ...drafts, [key]: value }))}
                onSave={requestGradeSave}
              />
            ) : (
              <div className="rounded-lg border border-dashed py-16 text-center text-muted-foreground">
                Select a student to view academic details.
              </div>
            )}
          </div>
        </TabsContent>

        <TabsContent value="courses" className="space-y-4 pt-4">
          {data.courses.map((course: any) => (
            <Card key={course.id}>
              <CardHeader className="pb-3">
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <Badge variant="secondary">{course.code}</Badge>
                    <CardTitle className="text-base">{course.name}</CardTitle>
                  </div>
                  <div className="text-sm text-muted-foreground">
                    {course.enrolled_count}/{course.capacity} seats
                  </div>
                </div>
              </CardHeader>
              <CardContent>
                <div className="mb-3 text-sm text-muted-foreground">
                  {course.doctor} - {course.days} - {course.time}
                </div>
                {course.students.length > 0 ? (
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>ID</TableHead>
                        <TableHead>Name</TableHead>
                        <TableHead>Email</TableHead>
                        <TableHead className="text-left">Grade</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {course.students.map((student: any) => (
                        <TableRow key={student.id}>
                          <TableCell>{student.student_id || "-"}</TableCell>
                          <TableCell>{student.name}</TableCell>
                          <TableCell>{student.email}</TableCell>
                          <TableCell className="text-left font-medium">
                            {student.grade ?? "Not recorded"}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                ) : (
                  <div className="text-sm text-muted-foreground">No students are enrolled yet.</div>
                )}
              </CardContent>
            </Card>
          ))}
        </TabsContent>

        <TabsContent value="users" className="pt-4">
          <Card>
            <CardContent className="p-0">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Name</TableHead>
                    <TableHead>Email</TableHead>
                    <TableHead>ID</TableHead>
                    <TableHead>Role</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.users.map((item: any) => (
                    <TableRow key={item.id}>
                      <TableCell>{item.name}</TableCell>
                      <TableCell>{item.email}</TableCell>
                      <TableCell>{item.student_id || "-"}</TableCell>
                      <TableCell>
                        <Badge variant={item.role === "admin" ? "default" : "secondary"}>
                          {item.role === "admin" ? "Admin" : "Student"}
                        </Badge>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="chat" className="pt-4">
          <Card>
            <CardContent className="flex items-center gap-3 p-6">
              <MessageSquare className="h-8 w-8 text-primary" />
              <div>
                <div className="text-2xl font-bold">{data.chat_messages_count}</div>
                <div className="text-sm text-muted-foreground">Stored chat messages</div>
              </div>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>

      <AlertDialog open={!!pendingGrade} onOpenChange={(open) => !open && setPendingGrade(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Confirm Grade Update</AlertDialogTitle>
            <AlertDialogDescription>
              {pendingGrade?.grade == null
                ? `This will clear the recorded grade for ${pendingGrade?.code}.`
                : `This will update ${pendingGrade?.code} to ${pendingGrade.grade}.`}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={(event) => {
                event.preventDefault();
                commitGrade();
              }}
            >
              {savingKey ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : null}
              Confirm
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </AppShell>
  );
}

function StudentDetail({
  student,
  gradeDrafts,
  savingKey,
  onDraftChange,
  onSave,
}: {
  student: AdminStudent;
  gradeDrafts: Record<string, string>;
  savingKey: string | null;
  onDraftChange: (key: string, value: string) => void;
  onSave: (student: AdminStudent, course: any) => void;
}) {
  return (
    <div className="space-y-4">
      <div className="grid gap-4 md:grid-cols-3">
        <Card>
          <CardContent className="p-5">
            <div className="text-sm text-muted-foreground">Name</div>
            <div className="mt-1 font-semibold">{student.profile.name}</div>
            <div className="mt-1 text-xs text-muted-foreground">{student.profile.email}</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-5">
            <div className="text-sm text-muted-foreground">Enrolled courses</div>
            <div className="mt-1 text-3xl font-bold">{student.academic_records.enrolled_courses}</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-5">
            <div className="text-sm text-muted-foreground">Average grade</div>
            <div className="mt-1 text-3xl font-bold">
              {student.academic_records.average_grade ?? "Not available"}
            </div>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Courses and Grades</CardTitle>
        </CardHeader>
        <CardContent>
          {student.enrolled_courses.length === 0 ? (
            <div className="rounded-lg border border-dashed py-10 text-center text-sm text-muted-foreground">
              This student is not enrolled in any courses.
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Course</TableHead>
                  <TableHead>Instructor</TableHead>
                  <TableHead>Schedule</TableHead>
                  <TableHead className="w-32">Grade</TableHead>
                  <TableHead className="w-24">Save</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {student.enrolled_courses.map((course) => {
                  const key = gradeKey(student.profile.id, course.course_id);
                  return (
                    <TableRow key={course.course_id}>
                      <TableCell>
                        <Badge variant="secondary" className="mb-1">
                          {course.code}
                        </Badge>
                        <div className="font-medium">{course.name}</div>
                      </TableCell>
                      <TableCell>{course.doctor}</TableCell>
                      <TableCell>{course.days} - {course.time}</TableCell>
                      <TableCell>
                        <Input
                          inputMode="decimal"
                          value={gradeDrafts[key] ?? ""}
                          placeholder="Grade"
                          onChange={(event) => onDraftChange(key, event.target.value)}
                        />
                      </TableCell>
                      <TableCell>
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => onSave(student, course)}
                          disabled={savingKey === key}
                        >
                          {savingKey === key ? (
                            <Loader2 className="h-4 w-4 animate-spin" />
                          ) : (
                            <Save className="h-4 w-4" />
                          )}
                        </Button>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function makeDrafts(students: AdminStudent[]) {
  const drafts: Record<string, string> = {};
  for (const student of students) {
    for (const course of student.enrolled_courses) {
      drafts[gradeKey(student.profile.id, course.course_id)] =
        course.grade == null ? "" : String(course.grade);
    }
  }
  return drafts;
}

function gradeKey(studentId: number, courseId: number) {
  return `${studentId}:${courseId}`;
}

function StatCard({ icon: Icon, label, value }: any) {
  return (
    <Card>
      <CardContent className="flex items-center gap-3 p-5">
        <div className="grid h-11 w-11 place-items-center rounded-xl bg-primary/10 text-primary">
          <Icon className="h-5 w-5" />
        </div>
        <div>
          <div className="text-2xl font-bold">{value}</div>
          <div className="text-xs text-muted-foreground">{label}</div>
        </div>
      </CardContent>
    </Card>
  );
}
