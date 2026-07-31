import type { ButtonHTMLAttributes } from "react";
import { cn } from "../../lib/cn";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "default" | "primary";
}

// Pattern lifted from style guide.html's real .btn/.btn.primary CSS — only two
// variants exist in the source material (outlined default + solid brand red).
export function Button({ variant = "default", className, ...props }: ButtonProps) {
  return (
    <button
      className={cn(
        "rounded-[10px] border px-3 py-2 text-[11px] font-bold transition-colors cursor-pointer",
        variant === "default" &&
          "border-border bg-card text-text hover:bg-panel",
        variant === "primary" &&
          "border-brand bg-brand text-white hover:bg-brand-hover hover:border-brand-hover",
        className,
      )}
      {...props}
    />
  );
}
