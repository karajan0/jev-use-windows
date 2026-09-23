"""Bounded UIA traversal with bulk property fetching, never cached actions."""

from types import SimpleNamespace


class CachedControl:
    def __init__(self, element, walker, request, auto, *, children_cached=False, siblings=None, index=0):
        self.element, self.walker, self.request, self.auto = element, walker, request, auto
        self.children_cached = children_cached
        self.siblings, self.index = siblings, index

    @property
    def ControlTypeName(self):
        return self.auto.ControlTypeNames.get(self.element.CachedControlType, "UnknownControl")

    @property
    def Name(self):
        return self.element.CachedName

    @property
    def BoundingRectangle(self):
        return self.element.CachedBoundingRectangle

    @property
    def IsEnabled(self):
        return self.element.CachedIsEnabled

    @property
    def IsOffscreen(self):
        return self.element.CachedIsOffscreen

    @property
    def IsPassword(self):
        return self.element.CachedIsPassword

    @property
    def HasKeyboardFocus(self):
        return self.element.CachedHasKeyboardFocus

    def GetRuntimeId(self):
        return self.element.GetCachedPropertyValue(self.auto.PropertyId.RuntimeIdProperty)

    def GetValuePattern(self):
        prop = self.auto.PropertyId
        if not self.element.GetCachedPropertyValue(prop.IsValuePatternAvailableProperty):
            return None
        return SimpleNamespace(
            Value=self.element.GetCachedPropertyValue(prop.ValueValueProperty),
            IsReadOnly=self.element.GetCachedPropertyValue(prop.ValueIsReadOnlyProperty),
        )

    def _wrap(self, element):
        return CachedControl(element, self.walker, self.request, self.auto) if element else None

    def GetFirstChildControl(self):
        element = self.element if self.children_cached else self.element.BuildUpdatedCache(self.request)
        children = element.GetCachedChildren()
        if not children or not children.Length:
            return None
        return CachedControl(children.GetElement(0), self.walker, self.request, self.auto, siblings=children)

    def GetNextSiblingControl(self):
        if self.siblings is None or self.index + 1 >= self.siblings.Length:
            return None
        index = self.index + 1
        return CachedControl(
            self.siblings.GetElement(index), self.walker, self.request, self.auto, siblings=self.siblings, index=index
        )


def root(handle):
    import uiautomation as auto
    from uiautomation.uiautomation import _AutomationClient

    client = _AutomationClient.instance().IUIAutomation
    request = client.CreateCacheRequest()
    request.TreeScope = 3  # Element + direct children, never the full subtree.
    request.TreeFilter = client.RawViewCondition
    for name in (
        "ControlType",
        "Name",
        "BoundingRectangle",
        "IsEnabled",
        "IsOffscreen",
        "IsPassword",
        "HasKeyboardFocus",
        "RuntimeId",
        "IsValuePatternAvailable",
        "ValueValue",
        "ValueIsReadOnly",
    ):
        request.AddProperty(getattr(auto.PropertyId, name + "Property"))
    element = client.ElementFromHandleBuildCache(handle, request)
    return CachedControl(element, client.RawViewWalker, request, auto, children_cached=True)
