"use client";

import * as React from "react";
import { motion, type HTMLMotionProps } from "framer-motion";
import { cn } from "@/components/ui/cn";

type Variant = "primary" | "secondary" | "ghost" | "danger" | "warning";
type Size = "sm" | "md" | "lg";

const base =
  "inline-flex min-h-10 items-center justify-center gap-2 rounded-xl text-sm font-semibold transition-all duration-200 " +
  "focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-blue-500/20 disabled:opacity-60 disabled:pointer-events-none";

const variants: Record<Variant, string> = {
  primary:
    "bg-slate-900 text-white shadow-sm hover:bg-slate-800",
  secondary:
    "bg-white text-slate-900 border border-slate-200 shadow-sm hover:bg-slate-50 hover:shadow-md",
  ghost: "bg-transparent text-slate-900 hover:bg-slate-50",
  danger:
    "bg-rose-50 text-rose-900 border border-rose-200 hover:bg-rose-100 shadow-sm",
  warning:
    "bg-orange-50 text-orange-900 border border-orange-200 hover:bg-orange-100 shadow-sm"
};

const sizes: Record<Size, string> = {
  sm: "px-3 py-2 text-xs",
  md: "px-4 py-2",
  lg: "px-4 py-3 text-base"
};

export type ButtonProps = Omit<HTMLMotionProps<"button">, "ref"> & {
  variant?: Variant;
  size?: Size;
  asChild?: boolean;
};

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = "secondary", size = "md", type, asChild, children, ...props }, ref) => {
    const classes = cn(base, variants[variant], sizes[size], className);
    if (asChild) {
      if (!React.isValidElement(children)) {
        throw new Error("Button `asChild` expects a single React element child.");
      }
      const child = children as React.ReactElement<{ className?: string }>;
      return (
        <motion.div whileTap={{ scale: 0.95 }} className="inline-block">
          {React.cloneElement(child, {
            className: cn(classes, child.props.className)
          })}
        </motion.div>
      );
    }
    return (
      <motion.button
        ref={ref}
        type={type ?? "button"}
        whileTap={{ scale: 0.95 }}
        className={classes}
        {...props}
      />
    );
  }
);
Button.displayName = "Button";

