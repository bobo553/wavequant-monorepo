import { redirect } from "next/navigation";

/** 将后台根路径收敛到默认工作台。 */
export default function HomePage(): never {
    redirect("/dashboard");
}
