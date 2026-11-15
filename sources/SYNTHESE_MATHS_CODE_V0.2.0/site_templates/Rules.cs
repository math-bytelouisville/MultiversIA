public static class ShippingRules {
    public static int BasePrice(int weight) { return weight <= 1000 ? 5 : 10; }
    public static int Max(int a, int b) { return a >= b ? a : b; }
    public static int Shipping(int weight, bool express) {
        return express ? Max(BasePrice(weight), 12) : BasePrice(weight);
    }
}
