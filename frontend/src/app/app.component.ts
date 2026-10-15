import { CurrencyPipe, DatePipe, NgFor, NgIf, TitleCasePipe } from '@angular/common';
import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { Component, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { firstValueFrom } from 'rxjs';

interface Account {
  id: number;
  full_name: string;
  email: string;
  role: 'buyer' | 'seller' | 'admin';
}

interface Product {
  id: number;
  seller_id: number;
  name: string;
  description: string;
  price: number;
  quantity: number;
}

interface OrderItem {
  product_id: number;
  seller_id: number;
  product_name: string;
  unit_price: number;
  quantity: number;
}

interface Order {
  id: number;
  total: number;
  status: string;
  payment_method: string;
  created_at: string;
  items: OrderItem[];
}

interface Payment {
  order_id: number;
  amount: number;
  status: string;
  method: string;
}

interface Notification {
  order_id: number;
  subject: string;
  message: string;
  delivery_status: string;
  created_at: string;
}

@Component({
  selector: 'market-root',
  standalone: true,
  imports: [CurrencyPipe, DatePipe, FormsModule, NgFor, NgIf, TitleCasePipe],
  templateUrl: './app.component.html',
})
export class AppComponent implements OnInit {
  private readonly http = inject(HttpClient);
  readonly cart: Record<number, number> = {};
  account: Account | null = null;
  products: Product[] = [];
  orders: Order[] = [];
  payments: Payment[] = [];
  notifications: Notification[] = [];
  search = '';
  email = '';
  password = '';
  fullName = '';
  registerRole: 'buyer' | 'seller' = 'buyer';
  deliveryAddress = '';
  productName = '';
  productDescription = '';
  productPrice: number | null = null;
  initialStock = 0;
  error = '';
  message = '';
  busy = false;

  async ngOnInit(): Promise<void> {
    await this.loadProducts();
    await this.restoreSession();
  }

  async restoreSession(): Promise<void> {
    if (!localStorage.getItem('market_access_token')) return;
    try {
      this.account = await firstValueFrom(this.http.get<Account>('/api/user/users/me'));
      await this.loadAccountData();
    } catch {
      this.logout();
    }
  }

  async loadProducts(): Promise<void> {
    try {
      const result = await firstValueFrom(
        this.http.get<{ items: Product[] }>('/api/product/products', {
          params: this.search.trim() ? { search: this.search.trim() } : {},
        }),
      );
      this.products = await Promise.all(
        result.items.map(async (product) => {
          const stock = await firstValueFrom(
            this.http.get<{ quantity: number }>(`/api/inventory/inventory/${product.id}`),
          );
          return { ...product, quantity: stock.quantity };
        }),
      );
    } catch (error: unknown) {
      this.error = this.errorText(error);
    }
  }

  async signIn(): Promise<void> {
    this.error = '';
    this.busy = true;
    try {
      const response = await firstValueFrom(
        this.http.post<{ access_token: string; user: Account }>('/api/user/auth/login', {
          email: this.email,
          password: this.password,
        }),
      );
      localStorage.setItem('market_access_token', response.access_token);
      this.account = response.user;
      this.password = '';
      await this.loadAccountData();
    } catch (error: unknown) {
      this.error = this.errorText(error);
    } finally {
      this.busy = false;
    }
  }

  async register(): Promise<void> {
    this.error = '';
    this.busy = true;
    try {
      await firstValueFrom(
        this.http.post('/api/user/auth/register', {
          full_name: this.fullName,
          email: this.email,
          password: this.password,
          role: this.registerRole,
        }),
      );
      this.message = 'Account created. Sign in to continue.';
      await this.signIn();
    } catch (error: unknown) {
      this.error = this.errorText(error);
    } finally {
      this.busy = false;
    }
  }

  logout(): void {
    localStorage.removeItem('market_access_token');
    this.account = null;
    this.orders = [];
    this.payments = [];
    this.notifications = [];
  }

  addToCart(product: Product): void {
    this.cart[product.id] = Math.min((this.cart[product.id] ?? 0) + 1, 100);
  }

  setQuantity(productId: number, quantity: number): void {
    if (quantity <= 0) delete this.cart[productId];
    else this.cart[productId] = Math.min(quantity, 100);
  }

  get cartItems(): Product[] {
    return this.products.filter((product) => (this.cart[product.id] ?? 0) > 0);
  }

  get cartTotal(): number {
    return this.cartItems.reduce(
      (total, product) => total + product.price * (this.cart[product.id] ?? 0),
      0,
    );
  }

  async checkout(): Promise<void> {
    if (!this.account || !this.deliveryAddress.trim() || this.cartItems.length === 0) return;
    this.error = '';
    this.busy = true;
    try {
      const productsBySeller = new Map<number, Product[]>();
      for (const product of this.cartItems) {
        const sellerProducts = productsBySeller.get(product.seller_id) ?? [];
        sellerProducts.push(product);
        productsBySeller.set(product.seller_id, sellerProducts);
      }
      const results = await Promise.allSettled(
        [...productsBySeller.entries()].map(async ([sellerId, products]) => {
          await firstValueFrom(
            this.http.post('/api/order/orders', {
              delivery_address: this.deliveryAddress,
              items: products.map((product) => ({
                product_id: product.id,
                quantity: this.cart[product.id],
              })),
            }),
          );
          return { sellerId, products };
        }),
      );
      const successfulOrders = results.flatMap((result) =>
        result.status === 'fulfilled' ? [result.value] : [],
      );
      for (const order of successfulOrders) {
        for (const product of order.products) delete this.cart[product.id];
      }
      await this.loadAccountData();
      await this.loadProducts();
      const failures = results.filter((result) => result.status === 'rejected');
      if (failures.length) {
        const failure = failures[0];
        this.error = `${successfulOrders.length} seller order(s) were placed; items for the remaining seller(s) are still in your basket. ${this.errorText(failure.reason)}`;
        if (!Object.keys(this.cart).length) this.deliveryAddress = '';
        return;
      }
      this.deliveryAddress = '';
      this.message = successfulOrders.length > 1
        ? `${successfulOrders.length} seller orders placed. Pay cash when they arrive.`
        : 'Order placed. Pay cash when it arrives.';
    } catch (error: unknown) {
      this.error = this.errorText(error);
    } finally {
      this.busy = false;
    }
  }

  async createProduct(): Promise<void> {
    if (this.productPrice === null) return;
    this.error = '';
    this.busy = true;
    try {
      const product = await firstValueFrom(
        this.http.post<Product>('/api/product/products', {
          name: this.productName,
          description: this.productDescription,
          price: this.productPrice,
        }),
      );
      await firstValueFrom(
        this.http.post('/api/inventory/inventory', {
          product_id: product.id,
          quantity: this.initialStock,
        }),
      );
      this.productName = '';
      this.productDescription = '';
      this.productPrice = null;
      this.initialStock = 0;
      this.message = 'Product listed and stock registered.';
      await this.loadProducts();
    } catch (error: unknown) {
      this.error = this.errorText(error);
    } finally {
      this.busy = false;
    }
  }

  async loadAccountData(): Promise<void> {
    if (!this.account) return;
    const requests = [
      firstValueFrom(this.http.get<Order[]>('/api/order/orders/me')),
      firstValueFrom(this.http.get<Payment[]>('/api/payment/payments/me')),
      firstValueFrom(this.http.get<Notification[]>('/api/notification/notifications/me')),
    ] as const;
    try {
      const [orders, payments, notifications] = await Promise.all(requests);
      this.orders = orders;
      this.payments = payments;
      this.notifications = notifications;
    } catch (error: unknown) {
      this.error = this.errorText(error);
    }
  }

  async updateOrderStatus(order: Order, status: string): Promise<void> {
    this.error = '';
    try {
      await firstValueFrom(
        this.http.patch(`/api/order/orders/${order.id}/status`, { status }),
      );
      this.message = `Order #${order.id} updated to ${status}.`;
      await this.loadAccountData();
    } catch (error: unknown) {
      this.error = this.errorText(error);
    }
  }

  paymentFor(orderId: number): Payment | undefined {
    return this.payments.find((payment) => payment.order_id === orderId);
  }

  private errorText(error: unknown): string {
    if (error instanceof HttpErrorResponse) {
      const detail: unknown = error.error?.detail;
      if (typeof detail === 'string') return detail;
      if (Array.isArray(detail)) return detail.map((item) => item.msg).join(', ');
      return error.status ? `Request failed (${error.status}).` : 'The API services are unavailable.';
    }
    return 'Something went wrong. Please try again.';
  }
}
