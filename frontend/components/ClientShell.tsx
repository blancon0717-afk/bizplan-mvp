"use client";
import ProgressNav from "@/components/ProgressNav";

export default function ClientShell({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <>
      <ProgressNav />
      <div className="ml-[200px]">{children}</div>
    </>
  );
}
