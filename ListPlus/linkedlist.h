#include <iostream>
#include <string>

using namespace std;

/* This class is going to create a circular doubly linked list with head and head node.
	The data should be able to be added and removed. */

class linkedlist
{
public:
	linkedlist();

	~linkedlist();

	void insert (int newItem);

	void remove (int dataItem);

	void print();

private:

	struct Node
	{
		int item;
		Node *next;
		Node *prev;
	};

	Node *listHead;
	Node *curr;

};
