import { useMe } from "@/api/queries";
import { RouteError, EmptyState } from "@/ui/states";
import { Content, ShellSkeleton, TopBar } from "./shell";
import { canReach, roleLabel, roleList, type NavItem } from "./nav";

/**
 * §B.8.3 · a route reached by a link the role cannot have **refuses inside the
 * content region**, naming the role it needs, and does not redirect silently --
 * a link that shows nothing is indistinguishable from a broken one.
 */
export function RoleGate({
  item,
  children,
}: {
  item: NavItem;
  children: React.ReactNode;
}) {
  const me = useMe();
  if (!me.data) return <ShellSkeleton />;
  if (!canReach(item, me.data.role)) {
    return (
      <>
        <TopBar breadcrumb={[]} title={item.label} />
        <Content>
          <RouteError
            title={`${item.label} requiere el perfil ${roleList(item.roles)}.`}
            detail={
              `Su sesión es de perfil ${roleLabel(me.data.role)}. Pida acceso a ` +
              "la propietaria de su droguería."
            }
          />
        </Content>
      </>
    );
  }
  return <>{children}</>;
}

/**
 * §B.10.2 · the **deliberately-empty** kind: a title naming what will live
 * there and a body naming what has to happen first, with no action -- because
 * S0 owns none of the actions that fill these seven routes, and a button that
 * does nothing is worse than a sentence that is true.
 */
export function StageRoute({
  item,
  title,
  emptyTitle,
  breadcrumb,
  body,
}: {
  item: NavItem;
  /** The route's one `t-28` title (§A.13.2). */
  title: string;
  /** What the empty state names -- never a second copy of the page title. */
  emptyTitle: string;
  breadcrumb: string[];
  body: string;
}) {
  return (
    <RoleGate item={item}>
      <TopBar breadcrumb={breadcrumb} title={title} />
      <Content>
        <EmptyState kind="deliberate" title={emptyTitle} body={body} />
      </Content>
    </RoleGate>
  );
}
