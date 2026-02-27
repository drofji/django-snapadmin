from django.shortcuts import render
from demo.models import Product

def product_search(request):
    query = request.GET.get('q', '')
    if query:
        # Using the new simplified snap_search method in SnapModel
        products = Product.snap_search(query)
    else:
        products = Product.objects.all()

    return render(request, 'demo/product_list.html', {
        'products': products,
        'query': query
    })
