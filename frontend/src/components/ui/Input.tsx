"use client";

import * as React from "react";
import { cn } from "@/components/ui/cn";

export type InputProps = React.InputHTMLAttributes<HTMLInputElement>;

export const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ className, ...props }, ref) => {
    return (
      <input
        ref={ref}
        className={cn(
          "w-full rounded-xl border border-slate-200 bg-white px-4 py-3 text-slate-900 outline-none " +
            "ring-blue-500/20 focus:ring-4 transition",
          className
        )}
        {...props}
      />
    );
  }
);
Input.displayName = "Input";

